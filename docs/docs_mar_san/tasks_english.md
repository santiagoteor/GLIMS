1. Internal route doesn't scale with trips (M1/M2)
- Issue: Internal km use a single TSP tour for all clients, but trunk distance is multiplied by number of trips; misrepresents multi-trip delivery when packages > capacity. 
- Goal: Coherent model where internal route is also split into multiple tours with CC reloading when capacity is exceeded.
- Location: simulador_osmnx.py, M1/M2 blocks (lines ~435-495), km_internos and viajes_1/viajes_2 variables.
- Solution: Split demand into batches of size capacity and calculate an internal route per batch, or apply a documented factor; explicitly report the assumption.

2. Resupply van emits as diesel in sustainable models
- Issue: In M3/M4/M5, CC→hub leg uses FURGONETA_CONV parameters (combustion cost and CO₂), but the spec defines sustainable trunk as electric van.
- Goal: Green scenarios should use the correct trunk vehicle to avoid inflating emissions.
- Location: simulador_osmnx.py, costo_camion_hub and co2_camion_hub (lines ~507-515) reused in M3/M4/M5.
- Solution: Parameterize trunk vehicle by scenario (electric in sustainable) instead of fixing FURGONETA_CONV.

3. M5 reuses pedestrian distance as car distance
- Issue: km_repartidor_pie (radial on drive network from centroid) is used for both foot delivery (M4) and estimating customer car CO₂ (M5, 25 g/km); routes and networks differ.
- Goal: Differentiated distances for pedestrian and customer vehicle, on coherent networks.
- Location: simulador_osmnx.py, km_repartidor_pie (line ~422) and M5 block co2_clientes (~591).
- Solution: Model customer travel separately (real PUDO catchment radius, appropriate network) and document modal split assumption.

4. Capacity and hub size mismatch with spec
- Issue: Code uses 60 packages/van; spec sets 300 packages/day and 50 m² ≈ 1,500 package microhub.
- Goal: Unique, spec-consistent, traceable parameters.
- Location: preparar_datos.py, PARAMETROS_MODELOS (lines 44-94); replicated in Barcelona.ipynb.
- Solution: Reconcile values with spec, leave parametros_modelos.csv as sole source, and annotate each number's origin.

5. 5-model logic duplicated in three places  
- Issue: Models, parameters, and calcular_haversine replicated in Barcelona.ipynb, simulador.py (unavailable), and simulador_osmnx.py; corrections require triplication.
- Goal: Single reusable implementation; notebooks only orchestrate and plot.
- Location: The three files mentioned.
- Solution: Consolidate into a parameterized simulador.py module and import from notebooks; eliminate copies.

6. Absolute Windows paths
- Issue: E:/UPV/Proyectos/GLIMS/... breaks reproducibility outside that machine.
- Goal: Relative/configurable and portable paths.
- Location: preparar_datos.py, RAW_DATA, ARCHIVO_PUNTOS, ARCHIVO_CC, CARPETA_SALIDA (lines 9-18).
- Solution: Derive from BASE_DIR or environment variables/config file; document expected folder structure.

7. Neighborhoods delimited by rectangles, not real polygons
- Issue: Bounding boxes may overlap (Eixample/Ciutat Vella) or leave gaps and don't match administrative boundaries.
- Goal: Filter by real neighborhood polygon.
- Location: preparar_datos.py, LIMITES_BARRIOS (26-42); simulador_osmnx.py, filtrar_puntos_barrio (97). Already explored in consultas.ipynb.
- Solution: Integrate features_from_place from consultas.ipynb to use polygons and point-in-polygon filtering.

8. Costly n×n distance matrix  
- Issue: Dense matrix built by running Dijkstra per subgraph node; scales poorly and mostly used only for TSP (radial only needs centroid row).
- Goal: Efficient distance calculation, scalable to 9 neighborhoods × 3 cities.
- Location: simulador_osmnx.py, construir_matriz_distancias (158-198) and obtener_matrices_barrio (201).
- Solution: Calculate only needed distances (multi-source or specific rows), cache results, and consider a route solver for TSP.

9. Dead code and debug prints
- Issue: Commented blocks and debug prints embedded in main flow clutter and confuse.
- Goal: Clean code with configurable logging instead of prints.
- Location: simulador_osmnx.py, commented distancia_cc_barrio (229-242), np.random.seed(42) (11), OSM:/HAV: prints (566-583).
- Solution: Remove dead code and replace prints with leveled logging.

10. Unfixed OSMnx version risk
- Issue: truncate_graph_bbox and nearest_nodes signatures changed between OSMnx 1.x and 2.x; unfixed version risks breakage.
- Goal: Reproducible environment with pinned dependencies.
- Location: simulador_osmnx.py, imports and construir_subgrafo_barrio (51-65); missing requirements.txt.
- Solution: Add requirements.txt/environment.yml with exact versions and validate used signature.

12. Unsourced emission factors
- Issue: 220 g CO₂/km diesel, 0 for electric/bike, 25 g/km customer; unreferenced and "0" for electric is tank-to-wheel.
- Goal: Cited factors with explicit criterion (TTW vs WTW).
- Location: preparar_datos.py, PARAMETROS_MODELOS (co2_km, co2_km_estimado_cliente).
- Solution: Cite sources (e.g., EEA/MITECO or LCA literature), justify emission scope, and discuss well-to-wheel in the paper.

13. Unsourced operating costs  
- Issue: €/km, €/h, 0.50€ PUDO fee, 45€/day microhub fixed are unsupported assumptions.
- Goal: Sourced or ranged costs, subject to sensitivity analysis.
- Location: preparar_datos.py, PARAMETROS_MODELOS (costo_km, costo_hora, comision_pudo, fijo_hub_dia).
- Solution: Document each value's provenance and add sensitivity on most influential ones.

14. Unjustified geometric heuristics
- Issue: 1.15 bike detour factor and radial approximation (centroid↔client round trip) for foot/PUDO delivery are unvalidated.
- Goal: Approximations justified or validated against real routes.
- Location: simulador_osmnx.py, km_bike_internos = km_internos * 1.15 (517) and calcular_distancia_radial (287).
- Solution: Validate against sample real routes, or replace with real routing from hub/PUDO location.

15. Uncited OSM network and Dijkstra/A* use
- Issue: Correct methodological decision but lacking bibliographic support in the paper.
- Goal: Cited method (OSMnx / Boeing 2017 and shortest-path literature).
- Location: simulador_osmnx.py, cargar_redes (36), construir_matriz_distancias (158), distancia_troncal with A* (386).
- Solution: Add these references in Methodology.

16. Unacknowledged heuristic nature of nearest neighbor TSP
- Issue: Suboptimal heuristic used without declaration or gap quantification.
- Goal: Routing declared as heuristic and compared with bound or solver.
- Location: simulador_osmnx.py, tsp_vecino_mas_cercano (245).
- Solution: Acknowledge it in the text and compare with OR-Tools or a lower bound on a sample.

17. Lacking unified, parameterized module
- Issue: Logic not encapsulated in a reusable, testable way.
- Goal: Modular simulator with single-responsibility, testable functions.
- Location: Entire project (dispersion between notebooks and scripts).
- Solution: Refactor to simulador.py with clear API (simular_ciudad, simular_barrio) and notebooks as presentation layer.

18. Noncentralized parameters as sole source of truth
- Issue: parametros_modelos.csv exists but values also hardcoded in the notebook.
- Goal: Single parameter origin consumed by all code.
- Location: preparar_datos.py (PARAMETROS_MODELOS) vs Barcelona.ipynb (PARAMETROS).
- Solution: Make everything read from CSV/config and eliminate notebook literals.

19. Unoptimized hub/PUDO location (centroid)
- Issue: Microhub/PUDO assumed at neighborhood centroid; no location decision, the core of the promised LRP (PR1/PR2).
- Goal: Optimal location among real candidate sites (parking, markets, post offices, metro, lockers).
- Location: simulador_osmnx.py, preparar_barrio (104) uses centroid as hub point.
- Solution: Define a set of candidate locations and solve the LRP location part.

20. Routing doesn't use Clarke-Wright + ILS as required by methodology
- Issue: Code does nearest neighbor; spec defines CWS multi-start + ILS metaheuristic with objective function penalizing emissions and vulnerable zones.
- Goal: LRP optimization core (CWS multi-start → ILS) justifying the "AI-based optimization" framework.
- Location: simulador_osmnx.py, tsp_vecino_mas_cercano (245); documentation: spec, Phase 1 Methodology.
- Solution: Implement CWS multi-start for initial solutions and ILS for improvement, with multi-objective cost/emissions/vulnerability objective function.

21. Fully deterministic model (no demand variability) 
- Issue: Single run per neighborhood; no stochasticity or Monte Carlo, despite PR3/PR4 mentioning demand variability and density.
- Goal: Repetitions with variable demand and confidence intervals.
- Location: simulador_osmnx.py, simular_ciudad (615) flow.
- Solution: Sample demand/volume scenarios, repeat, and aggregate with mean/deviation/CI.

22. No sensitivity analysis
- Issue: Conclusions depend on unaudited parameters.
- Goal: Sensitivity on fuel cost, emission factor, capacity, PUDO fee, and hub cost.
- Location: Nonexistent; to add in pruebas_osmnx.ipynb or analysis module.
- Solution: Parameter sweep and report on how winning model changes.

23. No route/distance validation
- Issue: OSM distance reliability not contrasted with independent source.
- Goal: Empirical route validation.
- Location: Nonexistent.
- Solution: Compare an OSM route sample against OSRM/Google or real data and report error.

24. Incomplete metrics vs spec
- Issue: Spec requests 10 disaggregated indicators (km by mode, trips by mode, emissions, cost); code aggregates only some.
- Goal: Report all 10 indicators by neighborhood and city.
- Location: simulador_osmnx.py, resultados.append({...}) dictionaries for each model.
- Solution: Expand output schema to separate km/trips by mode (van, bike, delivery foot, customer foot).

25. Missing urban density analysis (PR3)
- Issue: Results not crossed with neighborhood demand/population density.
- Goal: Analyze how cost/emissions vary with density (dense Eixample vs. sparse El Pardo).
- Location: Analysis in pruebas_osmnx.ipynb (uncovered).
- Solution: Add density per neighborhood and correlate with indicators.

26. Incomplete reproducibility
- Issue: Commented seeds, unfixed versions, config not saved with results.
- Goal: Bit-reproducible results.
- Location: simulador_osmnx.py (seed on line 11), missing requirements.txt, result saving in pruebas_osmnx.ipynb.
- Solution: Fix seeds, pin versions, and dump used config with each results CSV.

27. Nonexistent related work section
- Issue: Only 5 base references; no structured review.
- Goal: Systematic review (PRISMA, WoS/Scopus) on microhubs, PUDO, cargo-bike, and last-mile LRP.
- Location: Documentation (spec, references section); no paper draft.
- Solution: Execute the PRISMA review the spec itself promises and write the section.

28. Undeveloped formal paper methodology
- Issue: Missing LRP, objective function, and algorithm formalization; current code doesn't implement the described methodology.
- Goal: Methodology section with mathematical model, penalized objective function, and CWS+ILS description.
- Location: Documentation (spec, Phase 1 Methodology) vs actual code.
- Solution: Write formalization in parallel to implementing point 20.

29. Absent formal data description 
- Issue: Dataset not described (size by neighborhood, cleaning, representativeness).
- Goal: Data section with descriptive statistics and cleaning process.
- Location: preparar_datos.py (process) and outputs in data/; missing writeup.
- Solution: Document origin, volume per neighborhood/city, and cleaning criteria (limpiar_dataframe).

30. Missing experimental design
- Issue: There's a comparative run, but no design (repetitions, sensitivity, validation, significance).
- Goal: Paper-level replicable experimental protocol.
- Location: pruebas_osmnx.ipynb (exploration only).
- Solution: Define factors, repetitions, and metrics, and structure experiments accordingly.

31. Unconsolidated results
- Issue: Scattered exploratory plots, no final figures/tables or significance.
- Goal: Results figures and tables with indicator- and neighborhood-level reading.
- Location: pruebas_osmnx.ipynb, cells 4-19.
- Solution: Consolidate into a results notebook/figures with associated statistics.

32. Uncovered discussion
- Issue: No interpretation of which model wins, where, and why, or of the cost-emissions tradeoff.
- Goal: Discussion section interpreting results and their implications.
- Location: Nonexistent (pending paper draft).
- Solution: Write it based on consolidated results and cost-emissions scatter (cell 19).

33. Uncovered limitations
- Issue: Simplifications unacknowledged (radial, fixed capacity, no time windows, no traffic).
- Goal: Honest limitations section.
- Location: Nonexistent.
- Solution: Enumerate model assumptions and their potential impact on results.

34. Unwritten conclusions and policy implications
- Issue: Outlined in spec but not as paper section.
- Goal: Conclusions with implications for PMUS/E-DUM and recommendations.
- Location: Documentation (spec); missing in paper.
- Solution: Close with key findings and their public policy reading.