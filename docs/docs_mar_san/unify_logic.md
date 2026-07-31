
The task is unify the logic into a single parameterized module (simulator.py) and eliminate the duplication between the colab notebook and simulador_osmnx.py, following these steps:

Create a new **simulator.py** file that will be the unified core. Its initial public API could be:

python   def simulate_city(city, neighborhood=None):
       """Simulates a full city or a specific neighborhood."""
       pass

   def simulate_neighborhood(city, neighborhood, points, centers, parameters):
       """Simulates a neighborhood with the given points, centers, and parameters."""
       pass

   def load_data(city):
       """Loads data for a city (points, centers, limits, parameters)."""
       pass

- Move the common functions from simulador_osmnx.py to simulator.py, such as load_data, prepare_neighborhood, select_logistics_center, build_distance_matrix, etc. 
- Parameterize paths, file names, etc. Pass them as arguments or read them from environment variables / config files.
- Update simulador_osmnx.py to import and call simulator.py instead of having its own copy of the functions. In this case we can create a main function (simulator.py) and use different strategies in other functions. 
- Remove dead code, unused functions, and duplicated blocks.
- Write unit tests for the functions in simulator.py (e.g., using pytest) when it is operative. 
- Update pruebas_osmnx.ipynb to import from simulator.py instead of simulador_osmnx.py, and call it pruebas and just it. 