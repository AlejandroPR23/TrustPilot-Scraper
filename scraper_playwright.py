#!/usr/bin/env python3
"""
Trustpilot Rating Scraper - Versión Robusta con Playwright
==========================================================
Extrae SOLO la calificación general de páginas de Trustpilot.

Características:
- Playwright (renderiza JS correctamente, más resistente a cambios)
- Reintentos automáticos con backoff (tenacity)
- Logging completo (consola + archivo)
- CSV de entrada (lista de URLs)
- CSV histórico de salidas (append)
- JSON limpio para GitHub Pages (latest_ratings.json)
- Delays aleatorios respetuosos
- Manejo de errores por URL (no detiene todo el proceso)
- Estructura lista para consumir desde GitHub Pages

Uso:
    python scraper_playwright.py --input input_urls.csv --output-dir output
    python scraper_playwright.py --input "https://docs.google.com/spreadsheets/d/XXXX/pub?output=csv" --output-dir output

Instalación:
    pip install -r requirements.txt
    playwright install chromium
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout, Error as PlaywrightError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ====================== CONFIGURACIÓN ======================
DEFAULT_INPUT_CSV = "input_urls.csv"
DEFAULT_OUTPUT_DIR = "output"
LOG_FILE = "trustpilot_scraper.log"
MAX_RETRIES = 3
MIN_DELAY = 6
MAX_DELAY = 14
PAGE_TIMEOUT = 25000  # ms
# ===========================================================


def setup_logging(output_dir: Path) -> logging.Logger:
    """Configura logging a consola + archivo."""
    logger = logging.getLogger("trustpilot_scraper")
    logger.setLevel(logging.INFO)

    # Formato bonito
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Archivo
    log_path = output_dir / LOG_FILE
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def extract_domain_from_trustpilot_url(url: str) -> str:
    """Extrae el dominio o slug de la URL de Trustpilot."""
    try:
        path = urlparse(url).path
        if "/review/" in path:
            return path.split("/review/")[-1].strip("/")
        return path.strip("/")
    except Exception:
        return url


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1.5, min=4, max=20),
    retry=retry_if_exception_type((PlaywrightTimeout, PlaywrightError, ConnectionError)),
    reraise=True
)
def scrape_single_url(page, url: str, logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """
    Scrapea UNA URL usando Playwright (ya lanzado).
    Retorna dict con los datos o None si falla después de reintentos.
    """
    logger.info(f"Procesando: {url}")

    try:
        page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        # Pequeña espera extra por si hay lazy loading
        time.sleep(random.uniform(1.2, 2.5))

        content = page.content()
        soup = BeautifulSoup(content, "html.parser")

        # === Estrategia principal: __NEXT_DATA__ ===
        script = soup.find("script", id="__NEXT_DATA__")
        if script and script.string:
            data = json.loads(script.string)
            page_props = data.get("props", {}).get("pageProps", {})

            business = page_props.get("businessUnit")
            if not business and page_props.get("businessUnits"):
                bus = page_props["businessUnits"]
                business = bus[0] if isinstance(bus, list) else bus

            if business and isinstance(business, dict):
                trust_score = business.get("trustScore")
                num_reviews = business.get("numberOfReviews") or business.get("totalReviewCount") or business.get("reviewCount")

                result = {
                    "url": url,
                    "domain": extract_domain_from_trustpilot_url(url),
                    "trust_score": float(trust_score) if trust_score is not None else None,
                    "numero_reviews": int(num_reviews) if num_reviews is not None else None,
                    "estrellas": business.get("stars"),
                    "nombre": business.get("displayName") or business.get("name"),
                    "status": "ok",
                    "fuente": "__NEXT_DATA__"
                }
                logger.info(f"  ✓ OK → {result['trust_score']} ({result['numero_reviews']} reseñas)")
                return result

        # === Fallback: JSON-LD ===
        for ld_script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(ld_script.string or "{}")
                if isinstance(ld, dict):
                    agg = ld.get("aggregateRating", {})
                    if agg.get("ratingValue"):
                        return {
                            "url": url,
                            "domain": extract_domain_from_trustpilot_url(url),
                            "trust_score": float(agg.get("ratingValue")),
                            "numero_reviews": agg.get("reviewCount") or agg.get("ratingCount"),
                            "estrellas": None,
                            "nombre": ld.get("name"),
                            "status": "ok",
                            "fuente": "JSON-LD"
                        }
            except Exception:
                continue

        logger.warning(f"  ⚠ No se pudo extraer rating de {url}")
        return {
            "url": url,
            "domain": extract_domain_from_trustpilot_url(url),
            "trust_score": None,
            "numero_reviews": None,
            "estrellas": None,
            "nombre": None,
            "status": "no_data",
            "fuente": None
        }

    except PlaywrightTimeout:
        logger.error(f"  ✗ Timeout en {url}")
        raise
    except Exception as e:
        logger.error(f"  ✗ Error en {url}: {str(e)[:120]}")
        raise


def load_urls(input_source: str, logger: logging.Logger) -> List[str]:
    """
    Carga URLs desde:
    - Archivo CSV local
    - URL pública de Google Sheets publicado como CSV

    Soporta columna opcional 'active':
        - Si existe, solo incluye filas donde active sea TRUE / true / 1 / yes / si
    """
    try:
        if input_source.startswith(("http://", "https://")):
            logger.info(f"Cargando URLs desde Google Sheets: {input_source}")
            df = pd.read_csv(input_source)
            source_name = "Google Sheets"
        else:
            input_path = Path(input_source)
            if not input_path.exists():
                logger.error(f"No existe el archivo: {input_path}")
                sys.exit(1)
            df = pd.read_csv(input_path)
            source_name = str(input_path)

        # Normalizar nombre de columna 'url'
        if "url" not in df.columns:
            if len(df.columns) > 0:
                df = df.rename(columns={df.columns[0]: "url"})
            else:
                raise ValueError("El CSV debe tener al menos una columna con URLs")

        original_count = len(df)

        # === FILTRO POR COLUMNA 'active' (opcional pero muy útil) ===
        if "active" in df.columns:
            # Convertir a string y normalizar valores verdaderos
            active_col = df["active"].astype(str).str.lower().str.strip()
            true_values = {"true", "1", "yes", "si", "sí", "y", "verdadero"}

            df_active = df[active_col.isin(true_values)]
            filtered_count = len(df_active)

            if filtered_count < original_count:
                logger.info(f"Filtradas {original_count - filtered_count} filas inactivas (columna 'active')")

            df = df_active

        # Limpiar y validar URLs
        urls = (
            df["url"]
            .dropna()
            .astype(str)
            .str.strip()
            .tolist()
        )
        urls = [u for u in urls if u.startswith("http")]

        logger.info(f"Cargadas {len(urls)} URLs activas desde {source_name}")
        return urls

    except Exception as e:
        logger.error(f"Error cargando lista de URLs: {e}")
        sys.exit(1)


def save_history_csv(results: List[Dict], output_dir: Path, logger: logging.Logger):
    """Guarda/append al histórico CSV."""
    history_path = output_dir / "history.csv"
    df_new = pd.DataFrame(results)

    if history_path.exists():
        df_old = pd.read_csv(history_path)
        df_combined = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_combined = df_new

    df_combined.to_csv(history_path, index=False)
    logger.info(f"Histórico actualizado: {history_path} ({len(df_new)} nuevos registros)")


def save_latest_json(results: List[Dict], output_dir: Path, logger: logging.Logger):
    """Genera el JSON limpio para GitHub Pages."""
    clean_results = []
    for r in results:
        clean = {
            "url": r["url"],
            "domain": r.get("domain"),
            "trust_score": r.get("trust_score"),
            "numero_reviews": r.get("numero_reviews"),
            "estrellas": r.get("estrellas"),
            "nombre": r.get("nombre"),
            "status": r.get("status"),
            "last_checked": datetime.now(timezone.utc).isoformat()
        }
        clean_results.append(clean)

    payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_sites": len(clean_results),
        "successful": sum(1 for r in clean_results if r["status"] == "ok"),
        "ratings": clean_results
    }

    # Archivo con fecha
    dated_file = output_dir / f"ratings_{datetime.now().strftime('%Y-%m-%d')}.json"
    with open(dated_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # Archivo "latest" (el que consumirá GitHub Pages)
    latest_file = output_dir / "latest_ratings.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    logger.info(f"JSON generado para GitHub Pages: {latest_file}")
    logger.info(f"  → successful: {payload['successful']}/{payload['total_sites']}")
    return payload


def send_telegram_summary(payload: dict, output_dir: Path, logger: logging.Logger):
    """Envía resumen por Telegram usando Bot API (sin dependencias extra)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.info("Telegram no configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). Saltando notificación.")
        return

    try:
        import requests as req  # import local para no contaminar

        successful = payload.get("successful", 0)
        total = payload.get("total_sites", 0)
        last_updated = payload.get("last_updated", "")

        # Resumen corto
        text = (
            f"✅ *Trustpilot Scraper completado*\n\n"
            f"📊 Sitios procesados: *{total}*\n"
            f"✔️ Exitosos: *{successful}*\n"
            f"❌ Con error: *{total - successful}*\n\n"
            f"🕒 {last_updated[:16].replace('T', ' ')}\n\n"
            f"📁 `latest_ratings.json` actualizado"
        )

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }

        resp = req.post(url, data=data, timeout=15)
        if resp.status_code == 200:
            logger.info("📨 Resumen enviado por Telegram correctamente")
        else:
            logger.warning(f"No se pudo enviar Telegram: {resp.text[:200]}")

    except Exception as e:
        logger.error(f"Error enviando Telegram: {e}")


def main():
    parser = argparse.ArgumentParser(description="Trustpilot Rating Scraper (Playwright)")
    parser.add_argument("--input", "-i", default=DEFAULT_INPUT_CSV, 
                        help="Ruta a CSV local O URL de Google Sheets publicado como CSV")
    parser.add_argument("--output-dir", "-o", default=DEFAULT_OUTPUT_DIR, help="Carpeta de salida")
    parser.add_argument("--headless", action="store_true", default=True, help="Ejecutar sin ventana (recomendado)")
    parser.add_argument("--notify-telegram", action="store_true", help="Enviar resumen por Telegram al terminar (usa variables de entorno TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(output_dir)
    logger.info("=" * 60)
    logger.info("Trustpilot Scraper iniciado")
    logger.info("=" * 60)

    urls = load_urls(args.input, logger)
    if not urls:
        logger.error("No hay URLs válidas para procesar.")
        sys.exit(1)

    all_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768}
        )
        page = context.new_page()

        for i, url in enumerate(urls, 1):
            logger.info(f"[{i}/{len(urls)}]")

            try:
                result = scrape_single_url(page, url, logger)
                if result:
                    all_results.append(result)
            except Exception as e:
                logger.error(f"Falló después de {MAX_RETRIES} intentos: {url}")
                all_results.append({
                    "url": url,
                    "domain": extract_domain_from_trustpilot_url(url),
                    "trust_score": None,
                    "numero_reviews": None,
                    "estrellas": None,
                    "nombre": None,
                    "status": "error",
                    "fuente": None
                })

            # Delay respetuoso entre requests
            if i < len(urls):
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                logger.debug(f"Esperando {delay:.1f}s antes del siguiente...")
                time.sleep(delay)

        browser.close()

    if not all_results:
        logger.error("No se obtuvieron resultados.")
        sys.exit(1)

    # Guardar salidas
    save_history_csv(all_results, output_dir, logger)
    payload = save_latest_json(all_results, output_dir, logger)

    # Notificación Telegram (si se pidió o si las variables de entorno existen)
    if args.notify_telegram or (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        send_telegram_summary(payload, output_dir, logger)

    logger.info("=" * 60)
    logger.info("Proceso completado exitosamente")
    logger.info(f"Resultados en: {output_dir}")
    logger.info("  - history.csv          → Histórico completo")
    logger.info("  - latest_ratings.json  → Para GitHub Pages (usa este)")
    if args.notify_telegram or os.environ.get("TELEGRAM_BOT_TOKEN"):
        logger.info("  - Telegram           → Resumen enviado")
    logger.info("=" * 60)


if __name__ == "__main__":
    import os
    main()
