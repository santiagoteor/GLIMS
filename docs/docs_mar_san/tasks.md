1. Ruta interna no escala con los viajes (M1/M2)

Problema: los km internos son un único tour TSP de todos los clientes, pero el tramo troncal se multiplica por nº de viajes; si paquetes > capacidad, el reparto multi-viaje queda mal representado.
Aspirar a: un modelo de reparto coherente donde, al superar la capacidad, la ruta interna también se parta en varios tours con recarga en el CC.
Dónde: simulador_osmnx.py, bloques M1 y M2 (líneas ~435–495), variable km_internos y viajes_1/viajes_2.
Solución: dividir la demanda en tandas de tamaño capacidad y calcular una ruta interna por tanda, o aplicar un factor documentado; reportar el supuesto explícitamente.

2. La furgoneta de reabastecimiento emite como diésel en los modelos sostenibles

Problema: en M3/M4/M5 el tramo CC→hub usa los parámetros de FURGONETA_CONV (coste y CO₂ de combustión), cuando la memoria define el tronco sostenible como furgoneta eléctrica.
Aspirar a: que los escenarios verdes usen el vehículo troncal correcto para no inflar sus emisiones.
Dónde: simulador_osmnx.py, costo_camion_hub y co2_camion_hub (líneas ~507–515) reutilizados en M3/M4/M5.
Solución: parametrizar el vehículo del tronco por escenario (eléctrico en los sostenibles) en lugar de fijar FURGONETA_CONV.

3. M5 reutiliza la distancia peatonal como distancia en coche

Problema: km_repartidor_pie (radial sobre red drive desde el centroide) sirve a la vez para el reparto a pie (M4) y para estimar el CO₂ de clientes en coche (M5, 25 g/km); son trayectos y redes distintas.
Aspirar a: distancias diferenciadas para peatón y para vehículo del cliente, sobre redes coherentes.
Dónde: simulador_osmnx.py, km_repartidor_pie (línea ~422) y bloque M5 co2_clientes (~591).
Solución: modelar el desplazamiento del cliente aparte (radio real de captación del PUDO, red adecuada) y documentar el supuesto de modal split.

4. Capacidad y tamaño de hub no coinciden con la memoria

Problema: el código usa 60 paquetes/furgoneta; la memoria fija 300 paquetes/día y microhub de 50 m² ≈ 1.500 paquetes.
Aspirar a: parámetros únicos, consistentes con la memoria y trazables.
Dónde: preparar_datos.py, PARAMETROS_MODELOS (líneas 44–94); replicado en Barcelona.ipynb.
Solución: reconciliar valores con la memoria, dejar parametros_modelos.csv como única fuente y anotar el origen de cada número.

5. Lógica de los 5 modelos duplicada en tres sitios

Problema: modelos, parámetros y calcular_haversine están replicados en Barcelona.ipynb, simulador.py (no disponible) y simulador_osmnx.py; cualquier corrección hay que hacerla tres veces.
Aspirar a: una única implementación reutilizable; los notebooks solo orquestan y grafican.
Dónde: los tres archivos citados.
Solución: consolidar en un módulo simulador.py parametrizado e importar desde notebooks; eliminar las copias.

6. Rutas absolutas de Windows

Problema: E:/UPV/Proyectos/GLIMS/... rompe la reproducibilidad fuera de esa máquina.
Aspirar a: rutas relativas/configurables y portables.
Dónde: preparar_datos.py, RAW_DATA, ARCHIVO_PUNTOS, ARCHIVO_CC, CARPETA_SALIDA (líneas 9–18).
Solución: derivar de BASE_DIR o de variables de entorno/archivo de config; documentar la estructura de carpetas esperada.

7. Barrios delimitados por rectángulos, no por polígonos reales

Problema: los bounding boxes pueden solaparse (Eixample/Ciutat Vella) o dejar huecos y no corresponden a límites administrativos.
Aspirar a: filtrado por polígono real del barrio.
Dónde: preparar_datos.py, LIMITES_BARRIOS (26–42); simulador_osmnx.py, filtrar_puntos_barrio (97). Ya se explora en consultas.ipynb.
Solución: integrar features_from_place de consultas.ipynb para usar polígonos y un point-in-polygon en el filtrado.

8. Matriz de distancias n×n costosa

Problema: se construye una matriz densa lanzando un Dijkstra por nodo del subgrafo; escala mal y buena parte solo se usa para el TSP (la radial solo necesita la fila del centroide).
Aspirar a: cálculo de distancias eficiente y escalable a los 9 barrios × 3 ciudades.
Dónde: simulador_osmnx.py, construir_matriz_distancias (158–198) y obtener_matrices_barrio (201).
Solución: calcular solo las distancias necesarias (multi-source o filas concretas), cachear resultados y valorar un solver de rutas para el TSP.

9. Código muerto y prints de depuración

Problema: bloques comentados y prints de debug incrustados en el flujo principal ensucian y confunden.
Aspirar a: código limpio, con logging configurable en vez de prints.
Dónde: simulador_osmnx.py, distancia_cc_barrio comentada (229–242), np.random.seed(42) (11), prints OSM:/HAV: (566–583).
Solución: eliminar el código muerto y sustituir prints por logging con niveles.

10. Riesgo de versión de OSMnx sin fijar

Problema: truncate_graph_bbox y nearest_nodes cambiaron de firma entre OSMnx 1.x y 2.x; sin versión fijada el código puede romperse.
Aspirar a: entorno reproducible con dependencias pinneadas.
Dónde: simulador_osmnx.py, imports y construir_subgrafo_barrio (51–65); no hay requirements.txt.
Solución: añadir requirements.txt/environment.yml con versiones exactas y validar la firma usada.

12. Factores de emisión sin fuente

Problema: 220 g CO₂/km diésel, 0 para eléctrica/bici, 25 g/km cliente; sin referencia y "0" para eléctrica es tank-to-wheel.
Aspirar a: factores citados y con criterio (TTW vs WTW) explícito.
Dónde: preparar_datos.py, PARAMETROS_MODELOS (co2_km, co2_km_estimado_cliente).
Solución: citar fuentes (p. ej. EEA/MITECO o literatura LCA), justificar el alcance de emisiones y discutir well-to-wheel en el paper.

13. Costes operativos sin fuente

Problema: €/km, €/h, comisión PUDO 0,50 €, fijo microhub 45 €/día son supuestos sin respaldo.
Aspirar a: costes con fuente o rango, sometidos a análisis de sensibilidad.
Dónde: preparar_datos.py, PARAMETROS_MODELOS (costo_km, costo_hora, comision_pudo, fijo_hub_dia).
Solución: documentar procedencia de cada valor y añadir sensibilidad sobre los más influyentes.

14. Heurísticas geométricas sin justificar

Problema: el factor 1,15 de desvío en bici y la aproximación radial (ida y vuelta centroide↔cliente) para reparto a pie/PUDO no están validados.
Aspirar a: aproximaciones justificadas o validadas contra rutas reales.
Dónde: simulador_osmnx.py, km_bike_internos = km_internos * 1.15 (517) y calcular_distancia_radial (287).
Solución: validar contra rutas reales de una muestra, o sustituir por ruteo real desde la ubicación del hub/PUDO.

15. Uso de red OSM y Dijkstra/A* sin citar

Problema: decisión metodológica correcta pero sin respaldo bibliográfico en el paper.
Aspirar a: método citado (OSMnx / Boeing 2017 y literatura de shortest-path).
Dónde: simulador_osmnx.py, cargar_redes (36), construir_matriz_distancias (158), distancia_troncal con A* (386).
Solución: añadir estas referencias en Metodología.

16. TSP por vecino más cercano sin reconocer su carácter heurístico

Problema: se usa una heurística subóptima sin declararlo ni cuantificar el gap, aunque igual se debería usar otro heuristico. Investigar
Aspirar a: ruteo declarado como heurístico y comparado con una cota o solver, o usar otro. 
Dónde: simulador_osmnx.py, tsp_vecino_mas_cercano (245).
Solución: reconocerlo en el texto y comparar con OR-Tools o una cota inferior en una muestra.

17. Falta un módulo unificado y parametrizado

Problema: la lógica no está encapsulada de forma reutilizable y testeable.
Aspirar a: un simulador modular, con funciones de responsabilidad única y testeable.
Dónde: todo el proyecto (dispersión entre notebooks y scripts).
Solución: refactor a simulador.py con API clara (simular_ciudad, simular_barrio) y notebooks como capa de presentación.

18. Parámetros no centralizados como única fuente de verdad

Problema: existe parametros_modelos.csv pero los valores también viven hardcodeados en el notebook.
Aspirar a: un único origen de parámetros consumido por todo el código.
Dónde: preparar_datos.py (PARAMETROS_MODELOS) vs Barcelona.ipynb (PARAMETROS).
Solución: que todo lea del CSV/config y eliminar los literales del notebook.

19. Localización del hub/PUDO no optimizada (es el centroide)

Problema: el microhub/PUDO se asume en el centroide del barrio; no hay decisión de localización, que es el núcleo del LRP prometido (PR1/PR2).
Aspirar a: localización óptima entre ubicaciones candidatas reales (aparcamientos, mercados, correos, metro, lockers).
Dónde: simulador_osmnx.py, preparar_barrio (104) usa el centroide como punto del hub.
Solución: definir un conjunto de ubicaciones candidatas y resolver la parte de localización del LRP.

20. Ruteo no usa Clarke-Wright + ILS como exige la metodología

Problema: el código hace vecino más cercano; la memoria define CWS multi-start + metaheurística ILS con función objetivo que penaliza emisiones y zonas vulnerables.
Aspirar a: núcleo de optimización LRP (CWS multi-start → ILS) que justifique el marco de "optimización basada en IA".
Dónde: simulador_osmnx.py, tsp_vecino_mas_cercano (245); documentación: memoria, Metodología Fase 1.
Solución: implementar CWS multi-start para soluciones iniciales e ILS para mejora, con función objetivo multiobjetivo (coste/emisiones/vulnerabilidad).

21. Modelo totalmente determinista (sin variabilidad de demanda)

Problema: una sola corrida por barrio; no hay estocasticidad ni Monte Carlo, pese a que PR3/PR4 hablan de variabilidad de demanda y densidad.
Aspirar a: repeticiones con demanda variable e intervalos de confianza.
Dónde: simulador_osmnx.py, flujo de simular_ciudad (615).
Solución: muestrear escenarios de demanda/volumen, repetir y agregar con media/desviación/IC.

22. Sin análisis de sensibilidad

Problema: las conclusiones dependen de parámetros no auditados.
Aspirar a: sensibilidad sobre coste combustible, factor emisión, capacidad, comisión PUDO y coste hub.
Dónde: no existe; a añadir en pruebas_osmnx.ipynb o módulo de análisis.
Solución: barrido de parámetros y reporte de cómo cambia el modelo ganador.

23. Sin validación de distancias/rutas

Problema: no se contrasta la fiabilidad de las distancias OSM con una fuente independiente.
Aspirar a: validación empírica de las rutas.
Dónde: no existe.
Solución: comparar una muestra de rutas OSM contra OSRM/Google o datos reales y reportar el error.

24. Métricas incompletas frente a la memoria

Problema: la memoria pide 10 indicadores desglosados (km por modo, viajes por modo, emisiones, coste); el código agrega solo algunos.
Aspirar a: reportar los 10 indicadores por barrio y ciudad.
Dónde: simulador_osmnx.py, diccionarios resultados.append({...}) de cada modelo.
Solución: ampliar el esquema de salida para separar km/viajes por modo (furgoneta, bici, a pie repartidor, a pie cliente).

25. Falta análisis por densidad urbana (PR3)

Problema: no se cruzan resultados con densidad de demanda/población del barrio.
Aspirar a: analizar cómo varían coste/emisiones según densidad (Eixample denso vs. El Pardo disperso).
Dónde: análisis en pruebas_osmnx.ipynb (no cubierto).
Solución: añadir densidad por barrio y correlacionarla con los indicadores.

26. Reproducibilidad incompleta

Problema: semillas comentadas, versiones sin fijar, configuración no guardada junto a resultados.
Aspirar a: resultados reproducibles bit a bit.
Dónde: simulador_osmnx.py (seed en línea 11), ausencia de requirements.txt, guardado de resultados en pruebas_osmnx.ipynb.
Solución: fijar semillas, pinnear versiones y volcar la config usada junto a cada CSV de resultados.

27. Sección de trabajo relacionado inexistente

Problema: solo 5 referencias base; sin revisión estructurada.
Aspirar a: revisión sistemática (PRISMA, WoS/Scopus) sobre microhubs, PUDO, cargo-bike y LRP en última milla.
Dónde: documentación (memoria, apartado de referencias); no hay borrador de paper.
Solución: ejecutar la revisión PRISMA que la propia memoria promete y redactar la sección.

28. Metodología formal del paper sin desarrollar

Problema: falta la formalización del LRP, función objetivo y algoritmos; hoy el código no implementa la metodología descrita.
Aspirar a: sección de metodología con modelo matemático, función objetivo con penalizaciones y descripción de CWS+ILS.
Dónde: documentación (memoria, Metodología Fase 1) vs código real.
Solución: escribir la formalización en paralelo a implementar el punto 20.

29. Descripción formal de datos ausente

Problema: no se describe el dataset (tamaño por barrio, limpieza, representatividad).
Aspirar a: sección de datos con estadísticas descriptivas y proceso de limpieza.
Dónde: preparar_datos.py (proceso) y salidas en data/; falta redacción.
Solución: documentar origen, volumen por barrio/ciudad y criterios de limpieza (limpiar_dataframe).

30. Diseño experimental ausente

Problema: hay una corrida comparativa, pero sin diseño (repeticiones, sensibilidad, validación, significación).
Aspirar a: un protocolo experimental replicable de nivel paper.
Dónde: pruebas_osmnx.ipynb (solo exploración).
Solución: definir factores, repeticiones y métricas, y estructurar los experimentos en consecuencia.

31. Resultados sin consolidar

Problema: gráficos exploratorios dispersos, sin figuras/tablas finales ni significación.
Aspirar a: figuras y tablas de resultados con lectura por indicador y barrio.
Dónde: pruebas_osmnx.ipynb, celdas 4–19.
Solución: consolidar en un cuaderno/figuras de resultados con estadística asociada.

32. Discusión no cubierta

Problema: no hay interpretación de qué modelo gana, dónde y por qué, ni del trade-off coste-emisiones.
Aspirar a: sección de discusión que interprete los resultados y sus implicaciones.
Dónde: no existe (borrador de paper pendiente).
Solución: redactarla a partir de resultados consolidados y del scatter coste-emisiones (celda 19).

33. Limitaciones no cubiertas

Problema: no se reconocen las simplificaciones (radial, capacidad fija, sin ventanas horarias, sin tráfico).
Aspirar a: sección honesta de limitaciones.
Dónde: no existe.
Solución: enumerar supuestos del modelo y su impacto potencial en los resultados.

34. Conclusiones e implicaciones de política sin redactar

Problema: esbozadas en la memoria pero no como sección del paper.
Aspirar a: conclusiones con implicaciones para PMUS/E-DUM y recomendaciones.
Dónde: documentación (memoria); falta en el paper.
Solución: cerrar con hallazgos clave y su lectura para política pública.