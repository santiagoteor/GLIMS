- If we get the methodology of the guidebook EMEP/EEA (1.A.3.b.i-iv-Road-transport-2025.pdf) we can reach a solution: 

Diesel van 

- vehicle -> Light Commercial Vehicle
- Fuel -> Diesel
- Average speed -> (v_media)
- distance traveled -> calculated through osmnx and heuristic

The best solution is consult the BBDD of Emission Factor Database (https://efdb.apps.eea.europa.eu/?source=%7B%22track_total_hits%22%3Atrue%2C%22query%22%3A%7B%22match_all%22%3A%7B%7D%7D%2C%22display_type%22%3A%22tabular%22%7D) with this parameters: 

- NFR - sector: 1.A.3.b.ii Road transport, light duty vehicles
- Fuel: Diesel
- Pollulant: CO2
- Type: Tier 2 (most specific one)

Then, we can use that to obtain a model for a vehicle and use it in the project. 

For cargo bike
- Gruber et al. (2014) → How to model a distribution bicycle. 
- Conway et al. (2017) → Capacities and operations with microhubs. 
- CycleLogistics → Regular values and type of bicycles. 
- CITY CHANGER CARGO BIKE → European cases and specifications. 

For distributor on foot
- ISO 11228-1 → Limit of manual manipulation. 
- NIOSH Lifting Equation → Recommended weight. 
- EU-OSHA → Manual transport of weights.
- Search articles of last-mile delivery on foot to see how to convert those limits in operational capacity. 

All of the previous ones are on the folder 'articles' in this project. 

- Gruber, Kihm & Lenz (2014) use until 100 kg of weight, no package above 25 kg, and box until 176 liters (78×48×47 cm). 
It is a reference with many citations because come from the german real project "Ich ersetze ein Auto" with 41 e-cargo bikes

- Llorca y Moeckel asumen una capacidad de 20 paquetes por cargo bike eléctrica, mientras que Niels, Hof y Bogenberger asumen hasta 30 paquetes; y estudios de caso de Londres han impuesto la restricción de no más de 25 ítems por bici. En estudios de ciudades medianas se han modelado incluso escenarios con capacidades de carga de las e-cargo bikes entre 150 y 275 kg, pero eso ya son cargo bikes de 4 ruedas grandes. 