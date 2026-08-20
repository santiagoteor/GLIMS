# GLIMS Preprocessing Scripts

This document provides script-level documentation for the preprocessing utilities used by GLIMS. It complements the project-level [`README.md`](../../README.md), which describes the complete simulation workflow, methodology, experiment configuration, routing algorithms, and outputs.

The scripts documented here focus on preparing administrative boundaries, postal-address data, and reproducible demand instances. A QGIS export utility currently located in the same folder is documented separately at the end because it is a post-processing tool rather than a preprocessing dependency.

## Overview

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  Script                           Purpose                                                                                 Main output
  -------------------------------- --------------------------------------------------------------------------------------- -----------------------------------------------
  `arcGIS_valencia_portals.py`     Download Valencia portal locations from the Valencia Geoportal API                      `data/valencia/portals_valencia_full.geojson`

  `build_simulation_zones.py`      Normalize district/neighborhood boundaries and build the common simulation-zone layer   `data/<city>/zones_limits.geojson`

  `build_valencia_addresses.py`    Spatially enrich Valencia portal points with administrative and census-section codes    `data/valencia/direcciones_valencia.csv`

  `generate_instances_demand.py`   Generate reproducible synthetic customer-demand instances                               `results/<city>/demand/*.csv`

  `toQGIS.py`                      Export simulation routes and stops to QGIS-ready GeoPackages                            `<experiment_id>.gpkg`
  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------

A typical preprocessing dependency chain is:

``` text
Administrative source files
          │
          ▼
build_simulation_zones.py
          │
          └──────────────► data/<city>/zones_limits.geojson

Valencia Geoportal API
          │
          ▼
arcGIS_valencia_portals.py
          │
          ▼
portals_valencia_full.geojson
          │
          + administrative/census polygons
          │
          ▼
build_valencia_addresses.py
          │
          ▼
direcciones_valencia.csv
          │
          ▼
generate_instances_demand.py
          │
          ▼
results/<city>/demand/
```

The exact raw source files differ by city. Unless stated otherwise, commands should be executed from the project root.

------------------------------------------------------------------------

## Valencia Portal Download

### Script

``` text
code/preprocessing/arcGIS_valencia_portals.py
```

### Purpose

This script downloads the complete set of portal locations in Valencia from the Valencia Geoportal API and stores them as a GeoJSON dataset for downstream address preprocessing.

### Main implementation settings

-   `DATA_DIR`: base directory for city geographic data, imported from `code.common.paths`.
-   `BASE`: Valencia Geoportal API endpoint.
-   `EXPECTED`: expected feature count used as a download-completeness check.

### Processing workflow

1.  Query the Valencia Geoportal API using pagination, requesting up to 2,000 features per request.
2.  Request GeoJSON output in WGS84 (`EPSG:4326`).
3.  Use `objectid` to preserve stable ordering between requests.
4.  Retry failed requests up to three times.
5.  Continue until all available features have been retrieved.
6.  Combine the downloaded features into a single GeoJSON `FeatureCollection`.
7.  Save the dataset as `portals_valencia_full.geojson`.
8.  Compare the downloaded feature count against the expected reference value and report the result.

The main download logic is implemented in `fetch_all()`.

### Requirements

-   Internet access to the Valencia Geoportal API.
-   The required Python dependencies from the main project environment.

No local input file is required. The destination directory is created automatically when needed.

### Output

``` text
data/valencia/portals_valencia_full.geojson
```

The file is overwritten when the script is executed again.

### Usage

``` bash
python -m code.preprocessing.arcGIS_valencia_portals
```

No command-line arguments are required.

------------------------------------------------------------------------

## Simulation Zones Builder

### Script

``` text
code/preprocessing/build_simulation_zones.py
```

### Purpose

This script prepares district and neighborhood boundaries for Barcelona, Madrid, and Valencia from local governmental source files and combines both administrative levels into the common zone dataset used by GLIMS.

The process has two stages, and either stage can be skipped independently.

### Main implementation settings

-   `DATA_DIR`: base directory for processed city geographic data.
-   `RAW_DATA_DIR`: directory containing the local raw administrative sources; it can be overridden with `--raw-data-dir`.
-   `ADMIN_LEVELS`: maps administrative-level codes to their labels and output definitions.
-   `EXPECTED_COUNTS`: reference counts used as sanity checks in the log output.
-   `LOADERS`: dispatch table selecting the city-specific loader for each administrative level.
-   `SOURCES`: identifies the intermediate boundary files and their zone types.

### Stage 1 --- Load and normalize boundaries

This stage is skipped with `--skip-fetch`.

For each selected city and administrative level, the script:

1.  loads the corresponding governmental source dataset through a city-specific loader;
2.  removes records without valid geometry or zone names;
3.  reprojects the data to WGS84 (`EPSG:4326`);
4.  adds the administrative-level information;
5.  retains the normalized name, administrative level, and geometry;
6.  creates the city data directory when necessary;
7.  saves normalized district and neighborhood boundary files; and
8.  reports the number of processed zones against the configured reference count.

The normalized intermediate datasets use English filenames such as:

``` text
districts_limits.geojson
neighborhoods_limits.geojson
```

### Stage 2 --- Build the combined zone layer

This stage is skipped with `--skip-combine`.

The script loads the normalized district and neighborhood layers, standardizes their structure, assigns the common `zona` field and a `tipo` field identifying the administrative level, combines both datasets, and writes:

``` text
data/<city>/zones_limits.geojson
```

This is the standardized simulation-zone file consumed by the downstream GLIMS workflow.

### Required raw files

The raw administrative datasets are not generated by this script and must be sourced separately.

#### Madrid

``` text
raw_data/districts_madrid/DISTRITOS.shp
raw_data/neighborhoods_madrid/BARRIOS.shp
```

The corresponding Shapefile sidecar files (`.dbf`, `.shx`, `.prj`, etc.) must also be present.

#### Barcelona

``` text
raw_data/districts_barcelona/BarcelonaCiutat_Districtes.csv
raw_data/neighborhoods_barcelona/BarcelonaCiutat_Barris.csv
```

These datasets are expected to contain a `geometria_wgs84` WKT geometry column.

#### Valencia

``` text
raw_data/districts_valencia/distritos_valencia.geojson
raw_data/neighborhoods_valencia/barrios_valencia.geojson
```

A custom raw-data directory can be supplied through `--raw-data-dir`.

### Usage

Run both stages for one city:

``` bash
python -m code.preprocessing.build_simulation_zones --city madrid
```

Run both stages for all supported cities:

``` bash
python -m code.preprocessing.build_simulation_zones --city all
```

Run only the source-loading and normalization stage:

``` bash
python -m code.preprocessing.build_simulation_zones \
  --city all \
  --skip-combine
```

Reuse existing normalized layers and run only the combination stage:

``` bash
python -m code.preprocessing.build_simulation_zones \
  --city all \
  --skip-fetch
```

Use a custom raw-data location:

``` bash
python -m code.preprocessing.build_simulation_zones \
  --city all \
  --raw-data-dir /path/to/raw_data
```

------------------------------------------------------------------------

## Valencia Address Builder

### Script

``` text
code/preprocessing/build_valencia_addresses.py
```

### Purpose

This script cross-references Valencia portal points with district, neighborhood, and census-section polygons through spatial joins. It produces the normalized postal-address dataset required by the Valencia demand generator.

The output contains geographic coordinates in both WGS84 and ETRS89 / UTM zone 30N.

### Main implementation settings

-   `DATA_DIR`, `RAW_DATA_DIR`: project data directories.
-   `PORTALS_PATH`: Valencia portal-point input.
-   `DISTRICTS_PATH`: district polygon input.
-   `BARRIOS_PATH`: neighborhood polygon input.
-   `SECCIONS_PATH`: census-section polygon input.
-   `OUTPUT_PATH`: destination CSV.
-   `DISTRICT_CODE_COL`, `BARRIO_CODE_COL`, `SECCION_CODE_COL`: source administrative-code fields.
-   `STREET_CODE_COL`, `NUMBER_COL`: source portal attributes.
-   `ETRS89_UTM30N`: projected CRS used for metric coordinate columns.
-   `FINAL_COLUMNS`: final output schema and column order.

### Processing workflow

1.  Load the portal, district, neighborhood, and census-section layers.
2.  Normalize them to WGS84 (`EPSG:4326`), inferring a missing CRS from coordinate ranges when required.
3.  Retain point-type portal geometries and assign an internal row index.
4.  Perform point-in-polygon spatial joins to attach district, neighborhood, and census-section codes.
5.  Keep the first spatial match for points lying exactly on a shared polygon boundary.
6.  Compute WGS84 longitude/latitude and ETRS89 / UTM zone 30N coordinates.
7.  Assemble the normalized output schema.
8.  Report portals without district, neighborhood, or census-section matches.
9.  Remove portals missing any of the required administrative assignments.
10. Save the resulting address table.

Fields for which the Valencia source data has no direct equivalent are left empty in the normalized schema.

### Required inputs

The portal dataset must first be generated with `arcGIS_valencia_portals.py`:

``` text
data/valencia/portals_valencia_full.geojson
```

The following administrative and census-section layers must also be available:

``` text
raw_data/Valencia/distritos_valencia.geoJSON
raw_data/Valencia/barrios_valencia.geoJSON
raw_data/Valencia/secc_cens_valencia.geoJSON
```

These raw polygon datasets are external inputs and are not generated by this script.

### Output

``` text
data/valencia/direcciones_valencia.csv
```

This file is subsequently consumed by `generate_instances_demand.py`.

### Usage

From the project root:

``` bash
python code/preprocessing/build_valencia_addresses.py
```

No command-line arguments are required.

> **Note:** this script uses project-relative paths. Run it from the GLIMS project root. The `data/valencia/` destination directory must already exist; running `arcGIS_valencia_portals.py` first normally ensures that it does.

------------------------------------------------------------------------

## Demand Instance Generator

### Script

``` text
code/preprocessing/generate_instances_demand.py
```

### Purpose

This script generates reproducible synthetic customer-demand instances for Barcelona, Madrid, and Valencia.

Customer locations are sampled from real postal-address datasets using census-section population information. Sampling weights are designed so that addresses in each census section collectively represent that section's population. Parcel demand is then assigned according to the selected `low`, `medium`, or `high` scenario.

The methodological interpretation of demand scenarios, master samples, seeds, and nested instance sizes is described in the project-level [`README.md`](../../README.md). This section focuses on the script implementation and its input/output requirements.

### Main implementation settings

-   `PROJECT_ROOT`, `DATA_DIR`, `RAW_DATA_DIR`, `RESULTS_DIR`: common project paths imported from `code.common.paths`.
-   `CITY`: fallback city for implicit single-city execution.
-   `DEFAULT_CONFIG`: default demand-generation configuration path.
-   `RANDOM_SEED`: fallback reproducibility seed.
-   `DEMAND_SCENARIOS`: demand ranges for `low`, `medium`, and `high`.
-   `CUSTOMER_COUNTS`: fallback instance sizes.
-   `GEOGRAPHIC_CRS`: WGS84 (`EPSG:4326`) for output longitude/latitude.
-   `CITY_CONFIGS`: city-specific input paths, loaders, output directories, and schemas.

When a JSON configuration is used, its `scenarios`, `sizes`, `seeds`, and `overwrite` values control generation. The fallback module-level values are used only when the implicit single-city configuration is selected.

### Processing workflow

1.  Resolve the generation configuration from `--config`, `--city`, the default configuration file, or the fallback city settings.
2.  Load the selected city's postal-address dataset through its city-specific loader.
3.  Load census-section population data.
4.  Merge addresses with population by census section.
5.  Compute the address sampling weight:

``` text
section_population / addresses_in_section
```

6.  For each requested seed, draw a weighted sample without replacement up to the largest requested instance size.
7.  Save this population as a master customer sample.
8.  Construct each requested instance size from the first `size` records of the master sample, making smaller instances proper subsets of larger instances generated from the same seed.
9.  For every demand scenario, assign parcel demand using reproducible scenario/size-specific randomness.
10. Save the resulting demand instance.
11. Skip existing outputs unless overwriting is enabled.

### Required source data

#### Barcelona

``` text
raw_data/adreces_postals_elementals.csv
raw_data/2026_pad_mdbas.csv
```

#### Madrid

``` text
raw_data/madrid_direcciones_postales.csv
raw_data/madrid_poblacion.csv
```

#### Valencia

``` text
data/valencia/direcciones_valencia.csv
raw_data/Valencia/valencia_poblacion.csv
```

For Valencia, `direcciones_valencia.csv` must first be generated with `build_valencia_addresses.py`.

Except for the generated Valencia address table, these source datasets are external inputs and must be obtained separately.

### Optional configuration

The default configuration path is:

``` text
configs/demand/demand_generation.json
```

A demand-generation configuration can define:

``` text
cities
scenarios
sizes
seeds
overwrite
```

An alternative configuration can be supplied with `--config`.

### Outputs

Master customer samples follow the convention:

``` text
results/<city>/demand/master_customers_<size>_seed_<seed>.csv
```

Demand instances follow:

``` text
results/<city>/demand/demand_<scenario>_<size>_seed_<seed>.csv
```

The output directory is created automatically if necessary.

### Usage

Use the default demand configuration when available:

``` bash
python -m code.preprocessing.generate_instances_demand
```

Generate the default set for a single city:

``` bash
python -m code.preprocessing.generate_instances_demand --city madrid
```

Use an explicit configuration:

``` bash
python -m code.preprocessing.generate_instances_demand \
  --config configs/demand/demand_generation.json
```

For controlled model comparisons, reuse the same demand instance across the experiment configurations being compared.

------------------------------------------------------------------------

## Post-processing Utility: QGIS Export

### Script

``` text
code/preprocessing/toQGIS.py
```

### Purpose

`toQGIS.py` is located in the preprocessing package but is conceptually a **post-processing and visualization utility**. It converts GLIMS route and stop results into QGIS-ready GeoPackages.

The utility is not required to generate demand or execute a GLIMS simulation.

### Expected simulation outputs

Route CSVs are identified by the presence of a `geometry_wkt` column and are expected to include identifiers such as:

``` text
model
route_id
leg
experiment_id
```

Stop CSVs, typically `route_stops.csv`, are identified through fields including:

``` text
stop_id
latitude
longitude
model
route_id
leg
experiment_id
```

### Processing workflow

1.  Gather input CSV files from explicit files/directories or the configured default input directory.
2.  Classify each recognized CSV as a route or stop dataset from its header.
3.  Concatenate route files and stop files separately.
4.  Parse route WKT geometries into line features.
5.  Convert stop longitude/latitude coordinates into point features.
6.  Use WGS84 (`EPSG:4326`) for the resulting geospatial layers.
7.  Split outputs by `experiment_id` and logistics model (`M1`--`M5`).
8.  Assign route colors deterministically by `route_id` by default, or use a model-level color when route-based styling is disabled.
9.  Generate QGIS QML styles for route and stop layers.
10. Write model-specific route and stop layers into one GeoPackage per experiment.
11. Embed the generated style definitions in the GeoPackage so QGIS can apply them when the layers are loaded.

The resulting structure can contain layers such as:

``` text
M1_routes
M1_stops
M2_routes
M2_stops
...
M5_routes
M5_stops
```

### Output

One GeoPackage is produced per experiment:

``` text
<experiment_id>.gpkg
```

The output directory is created automatically when required.

### Styling options

By default, route identifiers receive deterministic route-specific colors.

To use one color per model instead:

``` bash
python code/preprocessing/toQGIS.py --no-by-route
```

### Usage

Using the script defaults:

``` bash
python code/preprocessing/toQGIS.py
```

Using explicit input and output locations:

``` bash
python code/preprocessing/toQGIS.py /path/to/results -o /path/to/output
```

> **Current implementation note:** the script documentation supplied with the project describes `CITY` as hardcoded to `madrid`, with a corresponding default input/output location. For other cities, use explicit input/output paths or update the script configuration. Because this utility consumes simulation results rather than demand inputs, explicit result paths are recommended when using it in analysis workflows.

------------------------------------------------------------------------

## Recommended Preprocessing Order

For Barcelona and Madrid, the usual preparation sequence is:

``` text
1. Obtain required administrative and population/address source files
2. build_simulation_zones.py
3. generate_instances_demand.py
4. Run GLIMS experiments
```

For Valencia, additional address preparation is required:

``` text
1. Obtain required administrative, census, and population source files
2. build_simulation_zones.py
3. arcGIS_valencia_portals.py
4. build_valencia_addresses.py
5. generate_instances_demand.py
6. Run GLIMS experiments
```

`toQGIS.py` is optional and runs only after simulation results exist:

``` text
GLIMS experiment
      │
      ▼
route / stop CSVs
      │
      ▼
toQGIS.py
      │
      ▼
GeoPackage
      │
      ▼
QGIS
```

------------------------------------------------------------------------

## Relationship with the Main Documentation

This README intentionally focuses on script-level behavior and data dependencies.

For the following topics, refer to the project-level [`README.md`](../../README.md):

-   GLIMS architecture and complete workflow;
-   logistics models M1--M5;
-   methodological demand-generation assumptions;
-   facility preprocessing and classification;
-   OSRM integration;
-   CWS and ILS routing algorithms;
-   experiment configuration;
-   running simulations; and
-   interpreting and validating experiment outputs.

Keeping these two documentation levels separate avoids duplicating methodological explanations while preserving the implementation details required to maintain and reproduce the preprocessing pipeline.
