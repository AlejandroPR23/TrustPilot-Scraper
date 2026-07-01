# Trustpilot Rating Scraper (Playwright)

Script robusto para extraer **únicamente la calificación general** de páginas de Trustpilot.  
Diseñado para bajo volumen (hasta ~100 sitios cada 1-2 semanas).

## Características

- **Playwright** → Renderiza JavaScript correctamente y es muy resistente a cambios de frontend.
- Reintentos automáticos con backoff exponencial (`tenacity`).
- Logging completo (consola + archivo).
- CSV de entrada (`input_urls.csv`).
- Histórico en `history.csv` (append).
- JSON limpio (`latest_ratings.json`) listo para consumir desde **GitHub Pages**.
- **Dashboard interactivo** (`dashboard.html`) con Chart.js y Tailwind.
- **Notificaciones por Telegram** al terminar (opcional).
- GitHub Actions listo para automatización semanal.
- Delays aleatorios respetuosos (6-14 segundos).
- No se detiene si falla una URL.

## Instalación

```bash
cd trustpilot-scraper

# 1. Crear entorno virtual (recomendado)
python -m venv .venv
source .venv/bin/activate          # Linux / WSL / macOS
# source .venv/Scripts/activate          # Windows

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Instalar navegador de Playwright (solo la primera vez)
playwright install chromium
```

## Uso rápido

```bash
# Opción 1: CSV local
python scraper_playwright.py --input input_urls.csv --output-dir output

# Opción 2: Desde Google Sheets (ideal para mantener la lista fácil)
python scraper_playwright.py --input "https://docs.google.com/spreadsheets/d/TU_ID/pub?gid=0&single=true&output=csv" --output-dir output

# Con notificación por Telegram
python scraper_playwright.py --input input_urls.csv --output-dir output --notify-telegram
```

### Salida generada

```
output/
├── history.csv                    # Histórico completo (se va acumulando)
├── latest_ratings.json            # ← Este es el que consumes desde GitHub Pages
├── ratings_2026-06-30.json        # Copia con fecha
├── trustpilot_scraper.log
└── dashboard.html                 # Dashboard bonito (cópialo junto al JSON)
```

## Usar Google Sheets como fuente de URLs (recomendado)

Esta es la mejor forma de mantener tu lista actualizada sin tocar archivos:

1. Crea un Google Sheet con al menos una columna llamada `url`.
2. **Archivo → Compartir → Publicar en la web** → elige formato **CSV**.
3. Copia la URL generada (debe terminar en `output=csv`).
4. Úsala directamente:

```bash
python scraper_playwright.py --input "https://docs.google.com/spreadsheets/d/1abc.../pub?gid=0&single=true&output=csv"
```

Funciona perfecto tanto localmente como en GitHub Actions.

### Filtro automático por columna `active` (nuevo)

El scraper detecta automáticamente si existe una columna llamada `active`.

- Solo procesa las filas donde `active` sea `TRUE`, `true`, `1`, `yes`, `si`, etc.
- Te informa cuántas filas inactivas fueron ignoradas.
- Si no existe la columna, usa todas las URLs (comportamiento normal).

Esto te permite mantener una lista grande y activar/desactivar sitios fácilmente sin borrar filas.

Ejemplo ideal de columnas:

| url                                      | notes                  | active | category    |
|------------------------------------------|------------------------|--------|-------------|
| https://www.trustpilot.com/review/amazon.com | Cliente importante    | TRUE   | E-commerce  |
| https://www.trustpilot.com/review/booking.com | Competidor            | FALSE  | Travel      |

---

## Estructura del JSON para GitHub Pages

El archivo `latest_ratings.json` tiene esta forma (fácil de consumir con `fetch`):

```json
{
  "last_updated": "2026-06-30T17:10:00+00:00",
  "total_sites": 4,
  "successful": 4,
  "ratings": [
    {
      "url": "https://www.trustpilot.com/review/amazon.com",
      "domain": "amazon.com",
      "trust_score": 4.2,
      "numero_reviews": 12450,
      "estrellas": 4,
      "nombre": "Amazon",
      "status": "ok",
      "last_checked": "2026-06-30T17:10:00+00:00"
    }
  ]
}
```

### Ejemplo de consumo desde GitHub Pages (JavaScript)

```js
// En tu sitio estático (GitHub Pages, Netlify, etc.)
fetch('https://tu-usuario.github.io/tu-repo/latest_ratings.json')
  .then(res => res.json())
  .then(data => {
    console.log(data.ratings);
    // Renderiza tarjetas, tabla, etc.
  });
```

## Automatización (cada 1-2 semanas)

### Opción 1: Cron en WSL / Linux / macOS

```bash
crontab -e
```

Añade esta línea (ejecuta todos los lunes a las 9:00 AM):

```cron
0 9 * * 1 cd /home/alejandro/trustpilot-scraper && /home/alejandro/trustpilot-scraper/.venv/bin/python scraper_playwright.py --input input_urls.csv --output-dir output >> output/cron.log 2>&1
```

### Opción 2: GitHub Actions (recomendado si quieres que el JSON se actualice solo)

Crea `.github/workflows/update-trustpilot.yml` en el mismo repo donde tienes GitHub Pages:

```yaml
name: Update Trustpilot Ratings

on:
  schedule:
    - cron: '0 9 * * 1'   # Todos los lunes 9:00 UTC
  workflow_dispatch:      # Permite ejecutarlo manualmente

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install chromium

      - name: Run scraper
        run: python scraper_playwright.py --input input_urls.csv --output-dir output

      - name: Commit and push JSON
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add output/latest_ratings.json
          git commit -m "Update Trustpilot ratings [$(date)]" || echo "No changes"
          git push
```

Esto actualiza automáticamente el JSON en tu repo de GitHub Pages.

## Notas importantes

- **Volumen bajo** → Con 100 sitios cada 2 semanas el riesgo de bloqueo es muy bajo.
- Trustpilot prohíbe el scraping en sus ToS. Úsalo bajo tu responsabilidad.
- Si prefieres una versión más ligera (sin navegador), puedo darte también la versión con solo `requests` + BeautifulSoup.
- El script extrae preferentemente desde `__NEXT_DATA__` (la forma más estable en 2026).

## ¿Quieres mejoras?

Dime si necesitas:
- Versión ligera solo con `requests`
- Envío automático por email o Telegram cuando termine
- Gráficos de evolución (Plotly / Chart.js)
- Filtro por dominio o categoría

¡Listo para usar!
