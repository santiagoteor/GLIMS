Los datos de Barcelona: qué tenemos y qué contienen

La base de datos principal es "Traffic state information by sections of the city of Barcelona" (TRAMS_TRAMS.dat), publicada en Open Data BCN. Cubre 527 tramos de calle de la ciudad, con histórico desde diciembre de 2017 y actualización cada 5 minutos. Cada registro contiene el identificador del tramo, la fecha completa (año, mes, día, hora, minuto, segundo), el estado actual de tráfico en una escala de 0 a 6 (0 = sin datos, 1 = muy fluido, 2 = fluido, 3 = denso, 4 = muy denso, 5 = congestión, 6 = cortado) y un estado previsto a corto plazo. Para poder entrenar un modelo predictivo también hacen falta las coordenadas de inicio y fin de cada tramo, que están disponibles en el propio dataset.

A esto se le añade el histórico meteorológico de Meteocat (red XEMA), que aporta precipitación por hora/día desde 2007, y que hay que cruzar con los datos de tráfico por fecha y hora, ya que no vienen integrados en el mismo dataset.

Existe un precedente directo de que este enfoque funciona en Barcelona: el estudio de Calvet y Carracedo (2024), que entrena un XGBoost sobre exactamente este dataset y consigue un área bajo la curva ROC superior al 80% prediciendo el estado de tráfico a partir de la hora, el día y la ubicación del tramo.

El problema de la conversión: de estado categórico a km/h

El dato de Barcelona es cualitativo (un nivel de congestión), pero el algoritmo de rutas necesita un tiempo de recorrido en minutos para calcular coste y comparar configuraciones. El Ajuntament no publica los umbrales de velocidad exactos que definen cada nivel —son internos de cada sensor—, así que hay que aproximarlos con un método externo y citable.

La vía más defendible es apoyarse en el Highway Capacity Manual (HCM), el estándar de referencia en ingeniería de tráfico, que define el nivel de servicio de una vía como un porcentaje de la velocidad en flujo libre: LOS A por encima del 85% de esa velocidad, LOS B entre 67-85%, LOS C entre 50-67%, LOS D entre 40-50%, y LOS E/F por debajo del 40-30%. Esta escala de seis niveles se corresponde razonablemente bien con la escala de Barcelona. Combinándola con los límites de velocidad reales de la ciudad (30 km/h en la mayoría de calles desde el decreto municipal, 50 km/h en las vías de la Xarxa Bàsica), se obtiene una tabla de conversión aproximada: muy fluido por encima de unos 25 km/h, fluido entre 20 y 25, denso entre 15 y 20, muy denso entre 12 y 15, y congestión por debajo de 12, para una calle limitada a 30 km/h. Si el tiempo lo permite, esta tabla se puede refinar cruzando una muestra de velocidades reales (por ejemplo de la API de Google Distance Matrix) con el estado categórico observado en esos mismos tramos y horas, para calibrar los valores específicamente para Barcelona en vez de depender solo del estándar genérico.

Qué se quiere lograr con todo esto

El objetivo no es predecir tráfico por predecirlo, sino conseguir que el algoritmo de optimización de rutas deje de asumir condiciones ideales fijas y evalúe cada configuración logística bajo la variabilidad real de tráfico y clima de Barcelona. Esto persigue dos cosas a la vez: primero, que las comparaciones entre las distintas alternativas de última milla (furgoneta directa, microhub más bici, PUDO más peatón, etc.) sean más realistas, mostrando no solo un coste medio sino también cuánto varía ese coste según las condiciones reales; y segundo, que la inteligencia artificial deje de ser un análisis decorativo y pase a formar parte del mecanismo de decisión del propio algoritmo, que es justo lo que pide el financiador del proyecto.

Los pasos del algoritmo completo

Fase 1, offline, antes de optimizar nada: se recopila el histórico de tráfico y clima de Barcelona, se entrena un modelo XGBoost que predice el estado de tráfico de un tramo a partir de su ubicación, la hora, el día de la semana, el día del mes y si llueve, se valida con el área bajo la curva ROC, y se aplica la tabla de conversión de estado a velocidad.

Fase 2, construcción de la ruta: el algoritmo de ahorro de Clarke-Wright asigna cada cliente al microhub o PUDO fijo más cercano con capacidad disponible, y construye una ruta inicial eficiente combinando clientes según el ahorro de fusionar sus visitas.

Fase 3, mejora iterativa: el ILS explora modificaciones sobre esa ruta inicial —reasignar un cliente, cambiar el orden de una visita— generando soluciones candidatas.

Fase 4, evaluación de cada candidata: para los tramos en furgoneta, se consulta el modelo XGBoost bajo varios escenarios de contexto representativos (por ejemplo, entre semana en hora punta, entre semana en valle, fin de semana con lluvia), ponderados según su frecuencia real; el estado predicho se convierte a velocidad con la tabla de conversión, y se simula varias veces (Monte Carlo) el tiempo, coste y emisiones resultantes. Para los tramos en bici o a pie, al no existir un dato equivalente de Barcelona, se usan velocidades calibradas de literatura en vez del modelo entrenado.

Fase 5, decisión: se agregan los resultados de la simulación en un valor esperado y una medida de riesgo, y ese conjunto es el fitness que el ILS usa para aceptar o rechazar la solución candidata, exactamente igual que decidiría con un valor fijo, solo que ahora ese valor refleja condiciones reales en vez de ideales.

Fase 6, repetición y comparación final: el proceso se repite hasta agotar el presupuesto de iteraciones, para cada una de las configuraciones logísticas que estáis comparando, y el resultado final de cada una no es un único número sino un coste/tiempo/emisiones esperado junto con su variabilidad bajo condiciones reales de Barcelona.