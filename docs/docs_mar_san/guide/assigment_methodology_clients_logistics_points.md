# Heurística de asignación cliente-instalación para el LRP de GLIMS

## Contexto

Dentro del WP1 (Fase 1) de GLIMS, el modelo logístico se resuelve como un problema de localización y rutas (LRP), que combina tres decisiones simultáneas: dónde ubicar las instalaciones (microhubs, PUDOs), qué cliente se asigna a cada instalación, y qué ruta sigue cada vehículo.

La memoria científico-técnica del proyecto especifica una metodología en dos etapas para esto:

> "Etapa inicial basada en heurístiques clàssiques per generar solucions de partida: s'utilitzarà l'heurística d'estalvi de Clarke-Wright (CWS) combinada amb un marc de múltiples inicis per assegurar diversitat de solucions. Les millors solucions es conservaran per a la segona etapa. Etapa de millora mitjançant una metaheurística de Cerca Local Iterada (ILS), que permetrà explorar múltiples barris i reassignar clients entre instal·lacions mantenint la viabilitat operativa (capacitat, distància, zones)."

Este documento describe la heurística propuesta para la parte de **asignación cliente → instalación** dentro de la etapa inicial, que sustituye a una asignación simple por proximidad.

## El problema de fondo

Idealmente, a un cliente se le debería asignar la instalación que minimice el coste de ruta resultante. Pero ese coste solo se conoce después de ejecutar el enrutamiento (CWS), lo que genera un problema circular: no se puede optimizar la asignación en función de algo que todavía no existe.

La solución adoptada no es resolver ese círculo en un solo paso, sino separarlo en dos fases, tal como ya prevé la memoria del proyecto:

1. **Construcción** (este documento): una asignación inicial razonable, rápida y diversa.
2. **Mejora (ILS)**: una vez conocido el coste real de las rutas, el ILS reasigna clientes entre instalaciones para corregir los errores de la asignación inicial.

Esto es coherente además con el comportamiento real de los clientes: en la práctica, cada cliente elige el PUDO/microhub más cercano, y ese criterio puede variar ligeramente entre repartos (no es una elección centralizada y perfectamente optimizada). La aleatorización sesgada (biased randomization) reproduce precisamente esa variabilidad real, además de servir como mecanismo de diversificación para el marco de múltiples inicios.

## Heurística propuesta: asignación por regret con aleatorización sesgada

### Terminología

Se usa el término **regret** (arrepentimiento / coste de oportunidad), no "savings" (para no confundirlo con el ahorro de Clarke-Wright usado después en el enrutamiento).

Para cada cliente *i*:

- *d_i(j)* = distancia del cliente *i* a la instalación *j*.
- *j₁(i)* = instalación más cercana al cliente *i* (mejor opción).
- *j₂(i)* = segunda instalación más cercana (segunda mejor opción), restringido a instalaciones con capacidad disponible.

**Regret:**

Δᵢ = d_i(j₂) − d_i(j₁) ≥ 0

- Δᵢ grande → cliente "cautivo" de su mejor instalación (poco importa el orden, casi no hay alternativa razonable).
- Δᵢ pequeño → cliente "ambiguo/flexible" (varias instalaciones le sirven casi igual de bien).

### Por qué se procesa por cliente y no por instalación

Procesar por instalación (cada PUDO eligiendo sus clientes preferidos) genera conflictos: dos instalaciones pueden "querer" al mismo cliente, obligando a resolver disputas de forma continua. Procesar por cliente evita el problema de raíz: cada cliente decide una única vez, sin competir con nadie.

### Algoritmo

1. Inicializar la capacidad restante *r_j = D_j* para cada instalación candidata *j*.
2. Para cada cliente sin asignar, calcular su lista de instalaciones ordenada por distancia (solo las que tienen *r_j > 0*) y su regret Δᵢ.
3. Seleccionar el cliente con mayor Δᵢ (el más urgente/cautivo).
4. Elegir su instalación mediante **aleatorización sesgada** sobre su lista ordenada (distribución geométrica, α ≈ 0.3–0.4, el mismo mecanismo ya usado en el CWS aleatorizado): casi siempre la primera opción, a veces la segunda, raramente la tercera.
5. Asignar el cliente, descontar su demanda de *r_j*, y quitarlo del conjunto de pendientes.
6. **Recalcular Δᵢ solo de los clientes cuyo mejor o segundo mejor candidato era la instalación que acaba de perder capacidad** (regret dinámico focalizado, no hace falta recalcular todo el conjunto).
7. Repetir desde el paso 3 hasta asignar a todos los clientes. Si algún cliente se queda sin ninguna instalación con capacidad, es la señal de abrir una instalación adicional (ya contemplado en el algoritmo exterior de selección de instalaciones).

### Nota sobre regret estático vs. dinámico

- **Estático**: calcular Δᵢ una sola vez al principio. Más rápido, pero puede quedar desactualizado si una instalación se llena a mitad de proceso.
- **Dinámico (recomendado)**: recalcular Δᵢ solo de los clientes afectados por el cambio de capacidad. Más preciso, coste computacional asumible a la escala de estas instancias (hasta 1000 clientes).

## Encaje en el pipeline de GLIMS

Esta heurística sustituye únicamente el paso de asignación dentro de la "etapa inicial" (construcción). El resto del pipeline no cambia:

1. Generación de instancias de clientes reales con demanda ponderada por población (ya implementado).
2. **Asignación cliente → instalación por regret + aleatorización sesgada (este documento).**
3. Enrutamiento con CWS (aleatorizado) dentro de cada submapa cliente-instalación.
4. Selección de las mejores soluciones del marco de múltiples inicios.
5. Mejora mediante ILS: reasignación de clientes entre instalaciones usando el coste de ruta real, respetando capacidad y zonas.

## Relación con la literatura

Esta propuesta no es una técnica inventada desde cero; conecta directamente con:

- El **criterio de ahorro marginal** ("marginal-savings criterion") ya citado en el artículo base de Castillo et al. (2024) para la asignación cliente-instalación en el mismo tipo de LRP.
- Los **regret-insertion heuristics** clásicos de la literatura de VRP (Potvin & Rousseau, 1993), origen del concepto de priorizar primero las decisiones con mayor coste de oportunidad.
- La **aleatorización sesgada (biased randomization / MIRHA)** ya empleada en el propio pipeline para el CWS de enrutamiento (Juan et al., 2013, 2015).

Enmarcarlo así (combinación de estas tres piezas para el sub-problema de asignación cliente-instalación dentro de una LRP) es una forma más defendible de presentar la contribución metodológica que "una asignación por proximidad relativa" inventada desde cero.

## Próximos pasos sugeridos

- Implementar la heurística en Python, integrada con las instancias de demanda ya generadas.
- Comparar contra dos líneas base: (a) asignación por proximidad simple (vecino más cercano), y (b) marginal-savings sin priorización por regret.
- Medir coste total de la mejor solución encontrada y velocidad de convergencia del ILS posterior, en varios tamaños de instancia y escenarios de demanda (bajo/medio/alto).
