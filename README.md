# Riesgo FEN · caudales diarios (SENAMHI)

Cada día a las 07:00 (Lima), GitHub Actions ejecuta `scripts/caudales.py`, que escribe `fen/caudales_hoy.json`. Netlify publica la carpeta `fen` junto con el dashboard.

- `scripts/estaciones_senamhi.json`: las 23 estaciones del corredor, con su código Phisis y su ficha.
- `scripts/caudales.py`: para cada estación obtiene la última lectura horaria (caudal o nivel), los umbrales amarillo/naranja/rojo de SENAMHI y la alerta resultante. Desde la vista diaria calcula además la anomalía frente al promedio histórico.
- `fen/index.html`: el dashboard público. Reemplázalo con cada versión nueva.

Datos en tiempo casi real, sin control de calidad (nota de SENAMHI).
