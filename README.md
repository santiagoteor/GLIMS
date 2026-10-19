# GLIMS: PCA 17/18, clústeres 2–7 y elbow con mínimo 2 por ciudad × arquetipo

## Instalación
Descomprimir desde la raíz de GLIMS. No se sobrescriben scripts previos.

## Preparación obligatoria
Reconstruir `data/ml/archetypes/urban_archetype_features_corregidas.csv` desde las **ubicaciones reales corregidas**, preservando el esquema del CSV anterior. Renombrar el CSV antiguo NO equivale a corregirlo. Auditar origen, microhub y PUDO.

## Ejecución
`bash code/ml/ejecutar_elbow_glims.sh`

Diagnóstico únicamente con datos históricos (NO válido para elegir la muestra):
`python code/ml/analizar_elbow_glims.py --features data/ml/archetypes/urban_archetype_features_all.csv --income data/ml/income_by_neighborhood.csv --allow-historical`

## Qué hace
- PCA de 17/18 variables, 3–8 PC y k=2..7 para diagnóstico, con cinco semillas.
- Para la selección de barrios: k=3 como **hipótesis**, 4/5/6 PC, α=0..1 cada 0.05.
- Mínimo de **2 barrios por ciudad×arquetipo**, para cada estrato existente; error si un estrato no tiene suficientes barrios.
- Excluye candidatos automáticos por encima de P95 de distancia al centroide de su estrato, pero los incluye en cobertura.
- Inicia con dos representantes de cada estrato, luego maximiza la reducción de suma de distancias al seleccionado más cercano **del mismo estrato**, ponderada por tamaño de estrato elevado a α.
- Evalúa todos los tamaños entre 27 y 70, siempre que sean factibles; el elbow es máxima distancia geométrica a la cuerda entre extremos normalizados.
- Genera `barrios_en_elbow.csv` para todas las combinaciones de 17/18, PC y α; no declara una única muestra ganadora.

## Resultados
`data/ml/archetypes/validacion_elbow_riguroso/`: `comparacion_k_2_a_7.csv`, `estabilidad_k.csv`, `varianza_*.csv`, `componentes_*.csv`, `curvas_cobertura.csv`, `elbows_por_alpha.csv`, `resumen_estabilidad_elbow.csv`, `clusters_por_barrio.csv`, `barrios_en_elbow.csv`, `MANIFIESTO.json`.

## Advertencias metodológicas
Este paquete recupera las restricciones documentadas, pero **no reproduce exactamente** el V3/V4 histórico ni el refinamiento Pareto. No hay validación externa NO2 ni evaluación de estabilidad de membresía bajo bootstrap; el ARI calculado evalúa variación entre semillas. El elbow depende del intervalo 27–70: se debe comparar con 27–45 y revisar sensibilidad. El número k=3 solo se usa para construir las muestras provisionales: si k≠3 gana en diagnóstico, hace falta ejecutar el muestreo para ese k antes de decidir. No usar listas para M1–M5 hasta revisión.
