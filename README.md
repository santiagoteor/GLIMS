# GLIMS

**Greening City Logistics: Innovative Sustainable Last-Mile Solutions**

## Overview

GLIMS is a simulation framework developed to evaluate and compare alternative strategies for sustainable urban last-mile logistics under a common and reproducible experimental environment.

The framework represents parcel distribution at customer level by combining simulated demand, existing logistics infrastructure, configurable delivery models, and road-network routing through the Open Source Routing Machine (OSRM).

Customer demand is generated beforehand as a spatially distributed set of customers and parcels. A generated demand instance is then used as a common input for all logistics models evaluated within the same experiment. This ensures that differences between models are caused by their operational design rather than by different customer-demand realisations.

GLIMS currently supports five last-mile delivery models, ranging from conventional and electric van delivery to alternative configurations based on microhubs, cargo bikes, Pick-Up and Drop-Off (PUDO) points, walking couriers, and customer collection.

The framework includes:

- configurable simulation of spatial customer demand;
- reusable demand instances for controlled model comparison;
- neighbourhood-level logistics simulation;
- selection and filtering of existing logistics facilities;
- customer-to-facility assignment;
- road-network routing through OSRM;
- multiple transport modes, including driving, cycling, and walking;
- capacity- and time-constrained vehicle routing;
- Clarke-Wright Savings (CWS) route construction;
- Iterated Local Search (ILS) route improvement;
- operational, economic, and environmental performance indicators;
- routing-integrity checks and experiment auditing;
- reproducible experiment configurations and metadata;
- detailed route-, facility-, and experiment-level outputs.

The current repository includes data-processing pipelines and experiment configurations for Barcelona, Madrid, and Valencia.

------------------------------------------------------------------------

## Simulation Architecture

GLIMS separates demand generation, experiment configuration, logistics simulation, routing optimisation, and result generation into different stages.

At a high level, the workflow is:

``` text
Spatial and logistics input data
              │
              ▼
       Demand generation
              │
              ▼
    Simulated demand instance
              │
              │
              ├──────── Experiment configuration
              │                  │
              └────────┬─────────┘
                       ▼
              GLIMS simulation
                       │
              ┌────────┴────────┐
              │                 │
        Study-area data    Logistics facilities
              │                 │
              └────────┬────────┘
                       ▼
              Zone preparation
                       │
              ┌────────┴────────┐
              │                 │
        Customer demand    Facility assignment
              │                 │
              └────────┬────────┘
                       ▼
                OSRM routing
                       │
                       ▼
              Route construction
                       │
              ┌────────┴────────┐
              │                 │
             CWS          Optional ILS
              │                 │
              └────────┬────────┘
                       ▼
                  Models M1–M5
                       │
                       ▼
              Routing validation
                       │
                       ▼
        Operational / economic /
         environmental indicators
                       │
                       ▼
             Results + metadata
```

This separation is important because several stages produce reusable outputs. In particular, demand generation is performed independently from the logistics simulation. A generated demand instance can therefore be reused across different models, routing configurations, random seeds, and experimental scenarios.

The detailed inputs, execution commands, parameters, dependencies, and outputs of each stage are documented in the corresponding sections of this README.

### 1. Demand generation

GLIMS uses simulated customer demand rather than generating a new customer population independently inside each logistics model.

The demand-generation pipeline creates spatial customer instances containing the customers and parcel demand required by subsequent experiments.

Demand generation is therefore a preprocessing stage:

``` text
Demand configuration
        +
Required spatial data
        │
        ▼
Demand-generation pipeline
        │
        ▼
Simulated demand instance
        │
        ▼
GLIMS experiment
```

A demand instance is generated once and stored so that it can be reused.

The same instance is then supplied to all five logistics models within a given experiment. This provides a controlled comparison in which M1–M5 operate on identical customer locations and parcel demand.

The complete demand-generation methodology, required files, configuration parameters, execution commands, and generated outputs are documented in the **Demand Generation** section.

### 2. Experiment configuration

A GLIMS experiment defines the conditions under which a generated demand instance is evaluated.

Experiments are normally described through JSON configuration files stored under:

``` text
configs/experiments/
```

These files centralise the main experimental choices, including the study area, demand instance, routing method, random seeds, simulation constraints, facility-selection behaviour, output options, and other experiment-specific settings.

For example:

``` text
configs/experiments/madrid_embajadores_ils_baseline.json
```

The experiment configuration is consumed by the main simulation entry point:

``` text
code/simulation/osrm_simulator.py
```

A configured experiment can be executed with:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/madrid_embajadores_ils_baseline.json
```

This section only introduces the role of the configuration file. A complete description of the supported parameters, defaults, and command-line options is provided later under **Experiment Configuration** and **Running GLIMS**.

### 3. Study-area preparation

Once an experiment starts, GLIMS loads the spatial and logistics information associated with the selected city and simulation zones.

The simulator identifies the customers belonging to each selected study area and prepares the operational information required by the five logistics models.

Selected neighbourhoods can be processed independently while using demand generated under a common city-level methodology.

### 4. Operational points and logistics facilities

The logistics models do not all use the same infrastructure.

Depending on the model, parcels may be delivered directly from a logistics centre or transferred through intermediate facilities such as microhubs or PUDOs.

The simulation therefore determines the operational points and candidate logistics facilities required by each model.

Where applicable, GLIMS can:

1.  determine the relevant operational point for the study area;
2.  identify eligible logistics facilities;
3.  restrict candidate facilities using configurable spatial criteria; and
4.  assign customer demand to the corresponding facilities.

These operations are performed before constructing the routing problems required by the corresponding logistics model.

### 5. Road-network routing

GLIMS uses the Open Source Routing Machine (OSRM) to obtain road-network distances and travel durations.

This avoids relying on straight-line distance as a representation of urban movement and allows routing to account for the underlying transport network.

The routing infrastructure is separated from the main simulation logic and is primarily implemented under:

``` text
code/routing/
```

Different routing profiles can be used for the transport modes involved in the logistics models.

The installation, preparation, execution, and expected availability of the OSRM services are documented separately under **OSRM Routing Infrastructure**.

### 6. Route construction and optimisation

Once the required road-network information is available, GLIMS constructs feasible distribution routes.

The routing layer currently includes two related components:

- **Clarke-Wright Savings (CWS)** for route construction; and
- **Iterated Local Search (ILS)** for optional improvement of the resulting routing solution.

CWS constructs routes while considering the operational constraints defined for the experiment.

When ILS is enabled, the initial solution is iteratively modified and reconstructed in an attempt to improve the routing objective while maintaining feasibility.

Routing behaviour is configurable and reproducible through the experiment configuration and associated random seeds.

The algorithms, constraints, parameters, and implementation details are described later under **Routing Algorithms**.

### 7. Logistics-model simulation

After the study area, demand, facilities, and routing information have been prepared, GLIMS evaluates the configured logistics models.

The five models represent different combinations of:

- logistics infrastructure;
- transport mode;
- consolidation strategy; and
- final-delivery mechanism.

Because all models receive the same simulated demand instance, their outputs can be compared under equivalent customer-demand conditions.

The methodology of M1–M5 is described in detail in the **Logistics Models** section.

### 8. Routing integrity and auditing

Route generation is followed by explicit integrity checks.

GLIMS verifies whether the expected customers and packages are represented consistently in the generated routing solution and records information about routing failures or inconsistencies.

These checks are exported alongside the experiment results so that routing validity can be evaluated independently from the performance indicators of the logistics models.

This distinction is important: a simulation result should only be interpreted after confirming that its routing solution satisfies the corresponding integrity checks.

### 9. Results and reproducibility

Each experiment generates a dedicated set of outputs.

Depending on the experiment configuration, these may include:

- model-level summary indicators;
- route-level results;
- route stops;
- facility-level results;
- routing-integrity reports;
- route geometries;
- execution-performance information;
- experiment configuration snapshots; and
- reproducibility metadata.

The metadata generated by the current framework can include information such as the Git commit and branch, working-tree status, configuration and demand hashes, Python version, execution platform, runtime, and experiment status.

The complete output structure and the interpretation of each result file are documented under **Outputs and Results**.

------------------------------------------------------------------------

## Repository Structure

The repository separates the main stages of the GLIMS workflow into dedicated modules and configuration directories.

``` text
GLIMS/
├── code/
│   ├── analysis/
│   ├── common/
│   ├── notebooks/
│   ├── preprocessing/
│   ├── routing/
│   ├── simulation/
│   └── traffic/
│
├── configs/
│   ├── demand/
│   ├── experiments/
│   └── osrm_sources.csv
│
├── data/
├── results/
├── scripts/
│
├── requirements.txt
└── README.md
```

### Main modules

| Directory              | Role in the project                                                                                                                                                                                                                  |
|:-----------------------|:-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `code/simulation/`     | Core logistics-simulation framework. It loads experiment configurations and generated demand, prepares study zones and facilities, executes M1–M5, validates routing results, calculates indicators, and exports experiment outputs. |
| `code/routing/`        | Routing and optimisation layer, including OSRM integration, Clarke-Wright Savings, Iterated Local Search, route-plan structures, and routing validation utilities.                                                                   |
| `code/preprocessing/`  | Preprocessing pipelines used to transform source data and generate inputs required by subsequent GLIMS stages, including simulated demand instances.                                                                                 |
| `code/analysis/`       | Supporting utilities for inspecting, validating, and analysing project datasets and intermediate results.                                                                                                                            |
| `code/common/`         | Shared project functionality such as paths, constants, cost calculations, routing helpers, and reusable utilities.                                                                                                                   |
| `code/notebooks/`      | Exploratory notebooks used for methodological development, preprocessing validation, and experiment/result analysis.                                                                                                                 |
| `code/traffic/`        | Experimental infrastructure for time-dependent traffic modelling.                                                                                                                                                                    |
| `configs/demand/`      | Configuration files controlling the generation of simulated customer-demand instances.                                                                                                                                               |
| `configs/experiments/` | Reproducible experiment definitions controlling the study area, demand instance, routing, simulation, facilities, random seeds, and output behaviour.                                                                                |
| `data/`                | Source, intermediate, and processed datasets required by the different GLIMS stages.                                                                                                                                                 |
| `results/`             | Outputs generated by simulation experiments and subsequent analyses.                                                                                                                                                                 |
| `scripts/`             | Supporting scripts for external infrastructure and project setup, particularly OSRM-related services.                                                                                                                                |

### Code and workflow relationship

The main relationship between the repository components can be summarised as:

``` text
configs/demand/
       │
       ▼
code/preprocessing/
       │
       ▼
Generated demand
       │
       │       configs/experiments/
       │               │
       └───────┬───────┘
               ▼
      code/simulation/
               │
        ┌──────┴──────┐
        ▼             ▼
 code/routing/     OSRM services
        │             │
        └──────┬──────┘
               ▼
           results/
```

Not every preprocessing script is required for every experiment. Some scripts prepare reusable datasets that only need to be generated once, while others produce experiment-specific inputs.

The **Data and Processing Pipeline** section documents these dependencies explicitly, including which scripts are independent, which consume the output of previous stages, and which generated files are required before a GLIMS experiment can be executed.

## Logistics Models

GLIMS compares five urban last-mile logistics models under a common simulated demand instance. The models represent alternative combinations of distribution infrastructure, vehicle technology, consolidation, and final-delivery strategy.

All models serve the same customer and parcel demand within an experiment. Their differences therefore arise from the logistics configuration used to move parcels from the logistics centre to their final destination.

At a conceptual level, the five models can be divided into two groups:

``` text
                         Logistics centre
                                │
               ┌────────────────┴────────────────┐
               │                                 │
               ▼                                 ▼
        Direct delivery                 Intermediate facility
           (M1–M2)                          (M3–M5)
               │                                 │
        ┌──────┴──────┐                 ┌────────┴────────┐
        │             │                 │                 │
        ▼             ▼                 ▼                 ▼
       M1            M2             Microhub            PUDO
 Conventional     Electric             │                 │
     van            van                ▼           ┌─────┴─────┐
        │             │             Cargo bike     │           │
        ▼             ▼                 │          ▼           ▼
    Customer      Customer              ▼         M4           M5
                                     Customer   Walking     Customer
                                               courier     collection
                                                  │
                                                  ▼
                                               Customer
```

| Model  | Logistics configuration                       | Main last-mile mode | Final service      |
|:-------|:----------------------------------------------|:--------------------|:-------------------|
| **M1** | Logistics centre → Customer                   | Conventional van    | Home delivery      |
| **M2** | Logistics centre → Customer                   | Electric van        | Home delivery      |
| **M3** | Logistics centre → Microhub → Customer        | Cargo bike          | Home delivery      |
| **M4** | Logistics centre → PUDO → Customer            | Walking courier     | Home delivery      |
| **M5** | Logistics centre → PUDO → Customer collection | Customer walking    | Collection at PUDO |

### M1 - Conventional Van Delivery

M1 represents the conventional direct-delivery scenario and serves as the reference logistics configuration.

Parcels originate at a logistics centre and are delivered directly to customers using conventional combustion vans. No intermediate consolidation facility is used between the logistics centre and the customer.

``` text
Logistics centre
       │
       │ Conventional van
       ▼
   Customers
```

The routing problem therefore consists of constructing van routes directly between the logistics centre and customer locations while satisfying the operational constraints defined for the experiment.

M1 provides the baseline against which the alternative vehicle and consolidation strategies can be compared.

------------------------------------------------------------------------

### M2 - Electric Van Delivery

M2 preserves the direct-delivery structure of M1 but replaces the conventional combustion van with an electric van.

``` text
Logistics centre
       │
       │ Electric van
       ▼
   Customers
```

The customer demand and logistics structure remain equivalent to M1. This allows the effects associated with vehicle electrification to be studied without simultaneously changing the underlying delivery network.

M1 and M2 therefore represent the same general direct home-delivery strategy with different vehicle technologies.

------------------------------------------------------------------------

### M3 - Microhub and Cargo-Bike Delivery

M3 introduces an intermediate consolidation stage between the logistics centre and the final customer.

Parcels are first transported from the logistics centre to selected microhubs. Customer demand is assigned to eligible microhubs, after which cargo bikes perform the final home-delivery routes.

``` text
Logistics centre
       │
       │ Supply vehicle
       ▼
    Microhub
       │
       │ Cargo bike
       ▼
   Customers
```

The model therefore contains two distinct distribution legs:

1.  **Facility supply:** parcels are transported from the logistics centre to the microhubs.
2.  **Cycling last mile:** cargo bikes depart from the microhubs and complete deliveries to customers.

This configuration represents a consolidated urban distribution strategy in which motorised access to the final delivery area can be reduced by transferring parcels to lower-impact vehicles for the last mile.

------------------------------------------------------------------------

### M4 - PUDO and Walking-Courier Delivery

M4 uses Pick-Up and Drop-Off (PUDO) facilities as intermediate consolidation points.

Parcels are transported from the logistics centre to selected PUDOs. Customers are assigned to eligible PUDOs, and walking couriers subsequently perform the final home-delivery stage.

``` text
Logistics centre
       │
       │ Supply vehicle
       ▼
      PUDO
       │
       │ Walking courier
       ▼
   Customers
```

As in M3, the distribution process contains two separate legs:

1.  **Facility supply:** parcels are transported from the logistics centre to PUDO facilities.
2.  **Walking last mile:** couriers depart from the PUDOs and deliver parcels to customer locations on foot.

M4 therefore retains home delivery while replacing the vehicle-based final distribution stage with a walking-courier operation.

------------------------------------------------------------------------

### M5 - Customer Collection from PUDO

M5 uses the same general PUDO-based consolidation concept as M4, but removes the courier-operated final-delivery stage.

Parcels are transported from the logistics centre to PUDO facilities. Customers then travel to their assigned PUDO to collect their parcels.

``` text
Logistics centre
       │
       │ Supply vehicle
       ▼
      PUDO
       ▲
       │ Customer round trip
       │
   Customers
```

The model therefore contains:

1.  **Facility supply:** parcels are transported from the logistics centre to PUDO facilities.
2.  **Customer collection:** customers travel between their location and the assigned PUDO to collect their parcels.

GLIMS explicitly represents the customer collection movement so that the displacement associated with transferring the final trip from the logistics operator to the customer is not ignored.

This distinction is important when comparing M5 with the other models: reduced logistics-operator travel does not necessarily imply reduced total system travel if customer collection trips are included in the evaluation.

------------------------------------------------------------------------

### Model Comparison

The five models progressively modify different components of the conventional last-mile distribution process:

| Characteristic                               | M1  | M2  |     M3     |     M4     |     M5     |
|:---------------------------------------------|:---:|:---:|:----------:|:----------:|:----------:|
| Direct logistics-centre-to-customer delivery |  ✓  |  ✓  |     –     |     –     |     –     |
| Intermediate facility                        | –  | –  |  Microhub  |    PUDO    |    PUDO    |
| Conventional van operations                  |  ✓  | –  |     –     |     –     |     –     |
| Electric van direct delivery                 | –  |  ✓  | Supply leg | Supply leg | Supply leg |
| Cargo-bike last mile                         | –  | –  |     ✓      |     –     |     –     |
| Walking-courier last mile                    | –  | –  |     –     |     ✓      |     –     |
| Customer collection                          | –  | –  |     –     |     –     |     ✓      |
| Home delivery                                |  ✓  |  ✓  |     ✓      |     ✓      |     –     |

The comparison is designed to separate several types of intervention:

- **vehicle substitution**, represented by M1 versus M2;
- **urban consolidation and cargo-bike delivery**, represented by M3;
- **PUDO-based consolidation with maintained home delivery**, represented by M4; and
- **PUDO-based consolidation with customer collection**, represented by M5.

The models are evaluated using common demand conditions and a common road-network routing infrastructure. Model-specific operational assumptions, capacities, service times, costs, and environmental parameters are handled by the corresponding simulation parameters and are documented separately from the conceptual model definitions.

### Distribution Legs in the Implementation

Internally, GLIMS distinguishes the different movements that constitute each logistics model. This is particularly important for M3–M5 because their total performance cannot be represented by a single route type.

The main distribution legs are:

| Model | Distribution leg      | Description                                                     |
|:------|:----------------------|:----------------------------------------------------------------|
| M1    | `direct_delivery`     | Conventional-van routes from the logistics centre to customers. |
| M2    | `direct_delivery`     | Electric-van routes from the logistics centre to customers.     |
| M3    | `facility_supply`     | Supply routes from the logistics centre to microhubs.           |
| M3    | `cycling_last_mile`   | Cargo-bike routes from microhubs to customers.                  |
| M4    | `facility_supply`     | Supply routes from the logistics centre to PUDOs.               |
| M4    | `walking_last_mile`   | Walking-courier routes from PUDOs to customers.                 |
| M5    | `facility_supply`     | Supply routes from the logistics centre to PUDOs.               |
| M5    | `customer_collection` | Customer round trips associated with parcel collection.         |

Keeping these legs separate allows GLIMS to report not only total model performance but also the contribution of each stage of the distribution chain.

Detailed routing constraints and optimisation procedures are described under **Routing Algorithms**, while the operational, economic, and environmental parameters used to evaluate each model are documented under **Experiment Configuration** and **Outputs and Results**.

## Installation

GLIMS requires a Python environment for the simulation and preprocessing modules and Docker for the OSRM routing services.

The project has been tested using both Windows and Linux environments. Unless otherwise indicated, commands should be executed from the root directory of the repository.

### Requirements

Before installing GLIMS, make sure the following software is available:

- **Python 3**
- **Git**
- **Docker**
  - Docker Desktop on Windows, or
  - Docker Engine on Linux
- Internet access during the initial OSRM setup, since OpenStreetMap extracts and the OSRM Docker image must be downloaded.

The current Python dependencies are listed in:

``` text
requirements.txt
```

The project also includes platform-specific scripts for preparing the OSRM infrastructure:

``` text
scripts/setup_osrm_city.ps1
scripts/setup_osrm_city.sh
```

------------------------------------------------------------------------

### 1. Clone the repository

Clone the project and move to its root directory:

``` bash
git clone https://github.com/santiagoteor/GLIMS
cd GLIMS
```

All subsequent commands in this README assume that the current working directory is the root of the GLIMS repository.

------------------------------------------------------------------------

### 2. Create a Python virtual environment

Using a virtual environment is strongly recommended so that the GLIMS dependencies remain isolated from the system Python installation.

Create the environment with:

``` bash
python -m venv .venv
```

#### Windows - PowerShell

Activate it with:

``` powershell
.\.venv\Scripts\Activate.ps1
```

After activation, the prompt should normally show:

``` text
(.venv)
```

#### Linux

Activate it with:

``` bash
source .venv/bin/activate
```

The environment must be activated again whenever a new terminal session is opened.

------------------------------------------------------------------------

### 3. Install Python dependencies

With the virtual environment active, upgrade `pip` and install the project dependencies:

``` bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The dependency file contains the Python packages required by the main simulation, routing, preprocessing, geospatial, optimisation, and data-processing components of GLIMS.

Among the main dependencies are:

- `numpy` and `pandas` for numerical and tabular data processing;
- `geopandas`, `shapely`, `pyproj`, and related geospatial libraries for spatial operations;
- `osmnx` and `networkx` for road-network and graph-related processing;
- `scikit-learn` for data-analysis and clustering utilities;
- `requests` for communication with external services such as OSRM; and
- `duckdb` for memory-efficient processing and aggregation of large datasets used by preprocessing workflows.

A basic installation can be checked with:

``` bash
python -c "import numpy, pandas, geopandas, osmnx, sklearn, shapely, requests, duckdb; print('GLIMS Python dependencies OK')"
```

If the command completes successfully and prints:

``` text
GLIMS Python dependencies OK
```

the main Python dependencies are available in the active environment.

The exact package list is maintained in:

``` text
requirements.txt
```

and should be installed again whenever that file is updated.

------------------------------------------------------------------------

## OSRM Routing Infrastructure

GLIMS uses the OSRM to obtain travel distances and travel times over the road network.

OSRM is not installed as a Python package. Instead, GLIMS runs separate OSRM servers inside Docker containers.

The repository automatically prepares three routing profiles for each supported city:

| Profile   | Purpose                        |
|:----------|:-------------------------------|
| `driving` | Motorised road transport       |
| `cycling` | Cargo-bike and bicycle routing |
| `walking` | Walking routes                 |

The currently configured cities are:

- Barcelona
- Madrid
- Valencia

The source OpenStreetMap extracts and city-specific port offsets are defined in:

``` text
configs/osrm_sources.csv
```

The generated OSRM data are stored under:

``` text
data/osrm/
```

------------------------------------------------------------------------

### OSRM service ports

Each city/profile combination runs as an independent local OSRM service.

| City      | Driving | Cycling | Walking |
|:----------|--------:|--------:|--------:|
| Madrid    |  `5000` |  `5001` |  `5002` |
| Barcelona |  `5010` |  `5011` |  `5012` |
| Valencia  |  `5020` |  `5021` |  `5022` |

For example, the Madrid driving service is available at:

``` text
http://localhost:5000
```

while the Barcelona cycling service uses:

``` text
http://localhost:5011
```

GLIMS selects the appropriate endpoint automatically according to the active city and routing profile.

------------------------------------------------------------------------

### 4. Prepare OSRM on Windows

Make sure Docker Desktop is running before executing the setup script.

From the project root:

``` powershell
.\scripts\setup_osrm_city.ps1
```

The script automatically:

1.  reads the configured cities from `configs/osrm_sources.csv`;
2.  downloads the required `.osm.pbf` files if they are not already available;
3.  prepares the `driving`, `cycling`, and `walking` OSRM datasets;
4.  runs the OSRM `extract`, `partition`, and `customize` stages;
5.  starts one Docker container for each city/profile combination.

Existing PBF downloads are reused by default.

To force the source files to be downloaded again:

``` powershell
.\scripts\setup_osrm_city.ps1 -ForceDownload
```

To prepare the routing datasets without starting the servers:

``` powershell
.\scripts\setup_osrm_city.ps1 -SkipStart
```

If the datasets have already been generated and only the Docker services need to be restarted:

``` powershell
.\scripts\setup_osrm_city.ps1 -SkipBuild
```

------------------------------------------------------------------------

### 5. Prepare OSRM on Linux

Make sure Docker is installed and that the current user can access the Docker daemon.

The setup script can then be executed from the repository root:

``` bash
./scripts/setup_osrm_city.sh
```

If the script is not executable yet:

``` bash
chmod +x scripts/setup_osrm_city.sh
./scripts/setup_osrm_city.sh
```

The Linux script performs the same main operations as the PowerShell version:

``` text
Download OSM extract
        │
        ▼
osrm-extract
        │
        ▼
osrm-partition
        │
        ▼
osrm-customize
        │
        ▼
Start osrm-routed container
```

The available options are:

``` bash
./scripts/setup_osrm_city.sh --help
```

Current options include:

``` text
--force-download   Re-download PBF files even when they already exist
--skip-build       Skip OSRM preprocessing and only start the servers
--skip-start       Build the datasets without starting the servers
```

------------------------------------------------------------------------

### 6. Check the OSRM containers

After the setup has completed, verify that the OSRM containers are running:

``` bash
docker ps
```

or, from PowerShell:

``` powershell
docker ps
```

The container names follow this structure:

``` text
osrm-<city>-<profile>
```

For example:

``` text
osrm-madrid-driving
osrm-madrid-cycling
osrm-madrid-walking

osrm-barcelona-driving
osrm-barcelona-cycling
osrm-barcelona-walking

osrm-valencia-driving
osrm-valencia-cycling
osrm-valencia-walking
```

Because the containers are created with Docker’s `unless-stopped` restart policy, they can normally be reused between GLIMS executions without rebuilding the routing datasets.

If the datasets already exist but the services are not running, use the setup script with the build stage disabled.

**Windows**

``` powershell
.\scripts\setup_osrm_city.ps1 -SkipBuild
```

**Linux**

``` bash
./scripts/setup_osrm_city.sh --skip-build
```

------------------------------------------------------------------------

### 7. Verify the GLIMS command-line interface

Once the Python environment is installed, the main simulator should expose its command-line interface:

``` bash
python -m code.simulation.osrm_simulator --help
```

A successful installation should display the available experiment options, including parameters related to:

- city and simulation zones;
- demand scenario and demand instance;
- OSRM routing profile;
- CWS or ILS routing;
- ILS configuration;
- route-geometry export;
- simulation date and shift;
- and experimental traffic-related settings.

This test verifies that the main GLIMS modules can be imported correctly. It does not run a complete simulation.

A complete experiment additionally requires:

``` text
Python environment
        +
Prepared input datasets
        +
Generated demand instance
        +
Running OSRM services
        +
Experiment configuration
        │
        ▼
GLIMS simulation
```

The preparation of those inputs is described in the following sections.

------------------------------------------------------------------------

### Installation versus data preparation

Installing the software and preparing a simulation experiment are intentionally treated as separate steps.

After completing this section, the user should have:

- a working Python virtual environment;
- the GLIMS Python dependencies;
- Docker available;
- the OSRM datasets prepared;
- and the required OSRM services running.

However, a simulation may still require city-specific processed datasets and a previously generated demand instance.

Those dependencies are described under **Data and Processing Pipeline** and **Demand Generation**.

## Data and Processing Pipeline

GLIMS separates raw source data, reusable processed datasets, simulated demand instances, and experiment outputs.

Not every preprocessing script must be executed before every simulation. Several stages generate reusable datasets that only need to be rebuilt when the underlying source data or methodology changes.

The main data flow is:

``` text
Raw / external data
        │
        ├──────────────────────────────┐
        │                              │
        ▼                              ▼
City data preparation          Facility-location review
        │                              │
        ▼                              ▼
data/<city>/...          results/<city>/location_review/
        │                       records_classified.csv
        │                              │
        ├───────────────┬──────────────┘
        │               │
        ▼               ▼
Administrative      Logistics facilities
boundaries
        │
        ▼
zones_limits.geojson
        │
        │
        ├──────────────────────────────┐
        │                              │
        ▼                              ▼
Demand generation               GLIMS simulation
        │                              ▲
        ▼                              │
results/<city>/demand/           Experiment config
demand_*.csv                           │
        │                              │
        └──────────────────┬───────────┘
                           ▼
                    M1–M5 experiment
                           │
                           ▼
                       results/
```

The sections below describe the role of each preprocessing component, its inputs, outputs, and dependencies.

------------------------------------------------------------------------

### Core simulation inputs

Before a standard GLIMS experiment can run, the simulator expects several reusable datasets.

For a selected city, the main loader expects:

``` text
data/<city>/centros_cc.csv
data/<city>/zones_limits.geojson
data/model_parameters.csv
```

The experiment also requires:

``` text
results/<city>/demand/<demand-instance>.csv
```

and the classified facility dataset:

``` text
results/<city>/location_review/records_classified.csv
```

Conceptually:

``` text
centros_cc.csv
        │
zones_limits.geojson
        │
model_parameters.csv
        │
records_classified.csv
        │
generated demand instance
        │
        ▼
GLIMS simulation
```

These files are produced by different preparation stages and do not necessarily need to be regenerated for every experiment.

------------------------------------------------------------------------

## City Data Preparation

### General city datasets

The main reusable city-level preparation script is:

``` text
code/preprocessing/prepare_data.py
```

Its purpose is to transform the original logistics infrastructure datasets into the common structure used by GLIMS.

The script currently prepares:

- B2C logistics points;
- logistics centres;
- a summary of B2C facilities;
- model parameters;
- and supporting city-level files.

It reads source files from:

``` text
raw_data/
```

and writes reusable processed files under:

``` text
data/
```

Typical outputs include:

``` text
data/
├── model_parameters.csv
├── resumen_b2c_por_company_type.csv
│
├── barcelona/
│   ├── puntos_b2c.csv
│   └── centros_cc.csv
│
├── madrid/
│   ├── puntos_b2c.csv
│   └── centros_cc.csv
│
└── valencia/
    ├── puntos_b2c.csv
    └── centros_cc.csv
```

The script is executed from the project root with:

``` bash
python -m code.preprocessing.prepare_data
```

This stage normally needs to be rerun only when the source logistics datasets or model-parameter definitions change.

> **Note**
>
> Some older helper outputs generated by this script are retained for compatibility or data inspection. Administrative polygon files used by the current simulator are handled separately through the boundary-processing workflow described below.

------------------------------------------------------------------------

## Administrative Boundaries

GLIMS uses polygonal administrative boundaries to define the spatial units available for simulation and to determine which customers and logistics facilities belong to the selected study areas.

The simulator requires a combined simulation-zone dataset for each city. This dataset contains the district and neighborhood polygons used throughout the spatial filtering and simulation workflow.

The main preprocessing entry point is:

``` text
code/preprocessing/build_simulation_zones.py
```

The script supports:

``` text
barcelona
madrid
valencia
all
```

Unlike the logistics simulation itself, this stage does not depend on OSRM. Its purpose is to transform city-specific administrative boundary sources into a common geographic structure that can be reused across experiments.

The general workflow is:

``` text
City-specific administrative sources
                │
                ▼
       Load source boundaries
                │
                ▼
      Normalize geometries
        and attribute fields
                │
                ▼
     Reproject to EPSG:4326
                │
        ┌───────┴────────┐
        ▼                ▼
    Districts       Neighborhoods
        │                │
        └───────┬────────┘
                ▼
       Combine zone levels
                │
                ▼
      Simulation-zone dataset
                │
                ▼
         GLIMS simulator
```

### Boundary source normalisation

Administrative boundaries are obtained from city-specific source datasets rather than assuming a single common source format for all cities.

The current implementation contains dedicated loaders for Barcelona, Madrid, and Valencia. These loaders convert the original administrative datasets into a common representation before they are used by the simulator.

Although the original formats differ between cities, the normalisation stage follows the same general procedure:

1.  load the district and neighborhood source datasets;
2.  remove records without valid geometry or zone names;
3.  normalize the relevant administrative attributes;
4.  reproject the geometries to WGS84 (`EPSG:4326`);
5.  store the normalized district and neighborhood layers under the corresponding city directory.

The expected numbers of administrative units defined in the implementation are used as sanity checks during preprocessing. They are not used to modify or artificially complete the source datasets.

Because the original administrative datasets are external inputs, they must be available before this preprocessing stage is executed.

Detailed information about the exact source files and city-specific loaders is maintained in the preprocessing documentation:

[`code/preprocessing/README.md`](code/preprocessing/README.md)

### Building the simulation-zone dataset

After the district and neighborhood boundaries have been normalized, the same script combines both administrative levels into the common zone dataset consumed by GLIMS.

The resulting combined dataset is stored as:

``` text
data/<city>/zones_limits.geojson
```

This is the standardized simulation-zone file used by the downstream GLIMS workflow.

The resulting records use a shared structure that identifies:

``` text
zona
tipo
admin_level
geometry
```

where:

- `zona` contains the normalized zone name;
- `tipo` distinguishes districts from neighborhoods;
- `admin_level` preserves the corresponding administrative level;
- `geometry` contains the polygon or multipolygon geometry.

This allows the simulator to work with both administrative levels through a common interface regardless of the original city-specific source format.

The complete workflow can be executed for a single city with:

``` bash
python -m code.preprocessing.build_simulation_zones --city madrid
```

or for all supported cities with:

``` bash
python -m code.preprocessing.build_simulation_zones --city all
```

The two stages can also be executed independently.

To perform only the source-loading and normalization stage:

``` bash
python -m code.preprocessing.build_simulation_zones \
  --city all \
  --skip-combine
```

To reuse previously normalized administrative layers and perform only the combination stage:

``` bash
python -m code.preprocessing.build_simulation_zones \
  --city all \
  --skip-fetch
```

A different raw-data directory can also be supplied when the administrative source files are stored outside the default project structure:

``` bash
python -m code.preprocessing.build_simulation_zones \
  --city all \
  --raw-data-dir /path/to/raw_data
```

In normal use, the administrative datasets are prepared once and reused across experiments. This stage only needs to be repeated when the underlying boundary sources change or when the processed geographic files need to be rebuilt.

The exact source filenames, intermediate outputs, loader behavior, validation checks, and implementation details are documented separately in:

[`code/preprocessing/README.md`](code/preprocessing/README.md)

## Facility Data Preparation and Classification

Logistics facilities used by M3–M5 are not read directly from the original B2C source spreadsheets during a simulation.

Instead, the simulation expects a reviewed and classified location dataset:

``` text
results/<city>/location_review/records_classified.csv
```

The loading stage explicitly requires this file and instructs the user to run the location-review workflow if it is missing.

The resulting classified dataset must contain, at minimum:

``` text
Record_Service_Type_Code
Latitude
Longitude
```

These service-type codes are later used to identify valid candidates for:

- microhubs; and
- PUDO facilities.

The relevant review workflow is implemented under:

``` text
code/analysis/
```

particularly through:

``` text
code/analysis/review_b2c_locations.py
```

The exact classification methodology and source reconciliation are treated as a data-quality stage rather than as part of the routing algorithm itself.

The output of this stage is therefore a dependency of the logistics simulation:

``` text
Raw / prepared B2C locations
        │
        ▼
Location review / classification
        │
        ▼
records_classified.csv
        │
        ▼
Microhub / PUDO candidate filtering
        │
        ▼
      M3–M5
```

------------------------------------------------------------------------

## Demand Generation Pipeline

GLIMS uses synthetic but spatially grounded demand instances to represent customer locations and parcel demand across the three study cities.

Demand generation is implemented through a common entry point:

``` text
code/preprocessing/generate_instances_demand.py
```

The same generator is used for:

``` text
Barcelona
Madrid
Valencia
```

Rather than maintaining separate demand-generation scripts for each city, the current implementation uses city-specific data loaders within a common generation pipeline.

The general workflow is:

``` text
City-specific address data
            +
Census population data
            │
            ▼
    City-specific loader
            │
            ▼
 Address-population matching
            │
            ▼
   Population-based weights
            │
            ▼
   Weighted master sample
            │
            ▼
 Nested customer instances
            │
            ▼
 Parcel-demand assignment
            │
            ▼
results/<city>/demand/
```

This design keeps the demand-generation methodology consistent across cities while allowing each study area to use the source datasets and preprocessing procedures appropriate to its available data.

### Demand-generation configuration

Demand generation can be controlled through a JSON configuration file.

The default configuration is:

``` text
configs/demand/demand_generation.json
```

The configuration can define the cities, demand scenarios, instance sizes, random seeds, and overwrite behavior used during generation.

Conceptually:

``` text
Demand configuration
        │
        ├── cities
        ├── scenarios
        ├── sizes
        ├── seeds
        └── overwrite
        │
        ▼
generate_instances_demand.py
```

This allows multiple reproducible demand instances to be generated systematically without modifying the preprocessing code.

### Running demand generation

From the project root, demand generation can be executed with:

``` bash
python -m code.preprocessing.generate_instances_demand
```

When the default demand-generation configuration is available, the script uses it to determine which cities, scenarios, instance sizes, and seeds should be generated.

A specific city can also be selected with:

``` bash
python -m code.preprocessing.generate_instances_demand --city madrid
```

An explicit configuration file can be supplied with:

``` bash
python -m code.preprocessing.generate_instances_demand \
  --config configs/demand/demand_generation.json
```

The generated demand instances are stored under:

``` text
results/<city>/demand/
```

with filenames following the convention:

``` text
demand_<scenario>_<size>_seed_<seed>.csv
```

For example:

``` text
results/madrid/demand/demand_high_100_seed_42.csv
```

Master customer samples are also stored so that the spatial customer selection associated with each seed can be preserved independently from the parcel-demand scenario.

Their filenames follow:

``` text
master_customers_<size>_seed_<seed>.csv
```

### Reproducible customer sampling

Customer locations are sampled from real address datasets using census-section population information.

For each census section, address sampling weights are derived conceptually as:

$$
w_i = \frac{P_s}{N_s}
$$

where:

- $P_s$ is the population of census section $s$;
- $N_s$ is the number of available addresses in that section;
- $w_i$ is the sampling weight assigned to an address belonging to that section.

This gives more populated census sections a proportionally larger contribution to the synthetic customer population while preserving the spatial distribution represented by the available address data.

For each random seed, the generator first produces a master customer sample corresponding to the largest requested instance size.

Smaller instances are then constructed from that same master sample:

``` text
Master sample — seed 42
        │
        ├── first 100 customers  → instance size 100
        ├── first 250 customers  → instance size 250
        ├── first 500 customers  → instance size 500
        └── ...
```

Consequently, for a fixed seed:

$$
C_{100} \subset C_{250} \subset C_{500} \subset \cdots
$$

where $C_n$ denotes the customer set of an instance containing $n$ customers.

This nested structure is useful for controlled experiments because differences between instance sizes are produced by adding customers rather than replacing the complete sampled population.

### Demand scenarios

After customer locations have been selected, parcel demand is assigned according to the configured demand scenario.

GLIMS currently distinguishes:

``` text
low
medium
high
```

The scenario controls the parcel-demand distribution applied to the sampled customers.

The three experimental dimensions should therefore be interpreted separately:

``` text
seed
  │
  └── controls the sampled customer population

instance size
  │
  └── controls how many customers are included

demand scenario
  │
  └── controls the parcel demand assigned to those customers
```

This distinction allows experiments to vary spatial demand, problem size, and parcel intensity independently.

For controlled comparisons between logistics models or routing algorithms, the same generated demand instance should be reused across the alternatives being compared.

### City-specific demand dependencies

Although Barcelona, Madrid, and Valencia use the same demand-generation script, the underlying source datasets differ between cities.

The common generator contains city-specific loaders responsible for transforming each city’s source data into the representation required by the shared demand-generation pipeline.

Conceptually:

``` text
Barcelona sources ──┐
                    │
Madrid sources ─────┼──► city-specific loaders
                    │            │
Valencia sources ───┘            ▼
                         common demand pipeline
                                  │
                                  ▼
                       standardized instances
```

This means that city-specific differences are handled at the data-loading and preprocessing level rather than through independent demand-generation programs.

Valencia requires an additional preprocessing step because its normalized address dataset is generated locally before demand instances can be created.

The relevant dependency is:

``` text
Valencia Geoportal
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
```

The resulting Valencia address dataset is stored as:

``` text
data/valencia/direcciones_valencia.csv
```

and is consumed by the Valencia loader inside the common demand generator.

Barcelona and Madrid use their corresponding address and population source datasets directly through their own loaders.

The exact source filenames, city-specific loader behavior, preprocessing requirements, and implementation details are documented separately in:

[`code/preprocessing/README.md`](code/preprocessing/README.md)

### Relationship with the simulator

Demand generation is independent from the logistics models and routing algorithms.

The generated demand instance becomes an input to the simulator:

``` text
generate_instances_demand.py
            │
            ▼
      Demand instance
            │
            ▼
      OSRM simulator
            │
     ┌──────┼──────┐
     ▼      ▼      ▼
   M1–M5   CWS    ILS
```

The simulator does not regenerate customer demand during model evaluation. Instead, it selects an existing demand instance according to the experiment configuration or command-line arguments.

This separation is important for experimental comparability: multiple logistics models or routing configurations can be evaluated using exactly the same customer locations and parcel demand.

Detailed methodological interpretation of the demand dimensions and their role in experimental design is provided later under **Demand Generation**. This section documents the preprocessing pipeline used to create those instances.

------------------------------------------------------------------------

## Valencia Address Preprocessing

Valencia requires an additional preprocessing stage before simulated demand can be generated.

The dependency chain is:

``` text
Valencia ArcGIS portal service
        │
        ▼
arcGIS_valencia_portals.py
        │
        ▼
portals_valencia_full.geojson
        │
        +
district / neighbourhood /
census-section polygons
        │
        ▼
build_valencia_addresses.py
        │
        ▼
direcciones_valencia.csv
        │
        ▼
generate_instances_demand.py
```

### Downloading Valencia portals

The script:

``` text
code/preprocessing/arcGIS_valencia_portals.py
```

downloads portal locations from the Valencia municipal ArcGIS service.

Run it with:

``` bash
python -m code.preprocessing.arcGIS_valencia_portals
```

The output is:

``` text
data/valencia/portals_valencia_full.geojson
```

The script retrieves the portal data in pages and performs a basic feature-count validation after the download.

------------------------------------------------------------------------

### Building the Valencia address dataset

Once the portal file and the required administrative layers exist, run:

``` text
code/preprocessing/build_valencia_addresses.py
```

with:

``` bash
python -m code.preprocessing.build_valencia_addresses
```

The script combines:

``` text
data/valencia/portals_valencia_full.geojson
raw_data/Valencia/distritos_valencia.geoJSON
raw_data/Valencia/barrios_valencia.geoJSON
raw_data/Valencia/secc_cens_valencia.geoJSON
```

using spatial point-in-polygon joins.

It generates:

``` text
data/valencia/direcciones_valencia.csv
```

which becomes the address input consumed by the Valencia demand generator.

The resulting file contains administrative identifiers and coordinates in both geographic and projected coordinate systems.

------------------------------------------------------------------------

## Dependency Summary

The minimum dependency chain for a standard experiment can be represented as:

``` text
                    CITY PREPARATION
                          │
          ┌───────────────┼─────────────────┐
          ▼               ▼                 ▼
 centros_cc.csv    zones_limits.geojson  model_parameters.csv
          │               │                 │
          └───────────────┼─────────────────┘
                          │
                          │
             FACILITY CLASSIFICATION
                          │
                          ▼
            records_classified.csv
                          │
                          │
               DEMAND GENERATION
                          │
                          ▼
        demand_<scenario>_<size>_seed_<seed>.csv
                          │
                          │
                 OSRM SERVICES
                          │
                          ▼
                GLIMS EXPERIMENT
```

In practical terms, a simulation can run once the following are available:

``` text
data/<city>/centros_cc.csv
data/<city>/zones_limits.geojson
data/model_parameters.csv

results/<city>/location_review/records_classified.csv

results/<city>/demand/
    demand_<scenario>_<size>_seed_<seed>.csv

running OSRM services
```

The experiment configuration then selects which city, zones, demand instance, routing method, and simulation settings are used.

------------------------------------------------------------------------

## Auxiliary Preprocessing Utilities

The repository also contains preprocessing utilities that are not part of the mandatory experiment pipeline.

Examples include:

``` text
json_to_csv.py
prepare_citypaq_import.py
toQGIS.py
```

These scripts support data conversion, CityPaq preparation, GIS export, and exploratory workflows.

They should be executed only when their corresponding output is required and are not prerequisites for every GLIMS experiment.

Similarly:

``` text
code/preprocessing/build_reference_profiles_15min.py
```

belongs to the experimental traffic-processing workflow. It uses DuckDB to process large traffic datasets efficiently and generate historical traffic reference profiles.

Traffic-aware simulation is still under methodological development, so this preprocessing branch is not currently required for the stable GLIMS workflow.

## Demand Generation

GLIMS uses simulated customer demand to provide controlled and reproducible inputs for the comparison of the five logistics models.

The technical demand-generation workflow, including its scripts, dependencies, configuration file, execution commands, and output locations, is described in **Data and Processing Pipeline**. This section focuses on how demand instances should be interpreted methodologically.

### Demand dimensions

A demand instance is defined by three independent dimensions:

| Dimension           | Controlled by   | Meaning                                                     |
|:--------------------|:----------------|:------------------------------------------------------------|
| Spatial realisation | `demand_seed`   | Determines the reproducible spatial sample of customers.    |
| Number of customers | `instance_size` | Determines how many simulated customers are included.       |
| Parcel intensity    | `scenario`      | Determines the number of parcels assigned to each customer. |

These dimensions should not be interpreted interchangeably.

The currently defined parcel-demand scenarios are:

| Scenario | Parcels per customer |
|:---------|---------------------:|
| `low`    |                  1–2 |
| `medium` |                  2–5 |
| `high`   |                 5–10 |

Consequently, `low`, `medium`, and `high` refer to parcel demand per customer, not to the total number of customers in the experiment.

For example:

``` text
demand_high_8000_seed_42.csv
       │      │       │
       │      │       └── spatial demand realisation
       │      └────────── 8,000 simulated customers
       └───────────────── high parcel intensity (5–10 parcels/customer)
```

This distinction allows customer population size and parcel intensity to be varied independently.

### Spatial demand generation

Customer locations are generated from city-specific address and population information.

Although the underlying source datasets differ between Barcelona, Madrid, and Valencia, the generators follow the same general principle:

``` text
Candidate customer locations
            +
     Population data
            │
            ▼
Spatial association
            │
            ▼
Population-based weights
            │
            ▼
Weighted customer sampling
```

Population information is therefore used to influence the spatial distribution of simulated customers rather than assuming that demand is uniformly distributed across the study area.

The resulting locations represent a synthetic but spatially informed customer population used as the basis for subsequent logistics experiments.

### Master customer samples

For each city and demand seed, GLIMS first constructs a reproducible master customer sample.

Conceptually:

``` text
Master customer sample - seed 42
              │
      ┌───────┼────────┐
      ▼       ▼        ▼
   size A   size B   size C
```

Requested instance sizes are derived from this common seeded population rather than being independently resampled.

For a given seed, smaller instances therefore form subsets of the same spatial customer realisation used by larger instances.

This design improves comparability when studying the effect of increasing the number of customers because changes in instance size do not simultaneously introduce an entirely different spatial sample.

### Parcel assignment

After the spatial customer sample has been selected, the chosen demand scenario determines the parcel demand associated with each customer.

For a customer $i$, the generated demand can be represented as:

$$
q_i \in
\begin{cases}
[1,2], & \text{low} \\
[2,5], & \text{medium} \\
[5,10], & \text{high}
\end{cases}
$$

where $q_i$ is the number of parcels assigned to customer $i$.

The resulting demand instance therefore combines:

``` text
WHO / WHERE?
Spatial customer sample
        │
        │
        ▼
HOW MANY CUSTOMERS?
Instance size
        │
        │
        ▼
HOW MANY PARCELS?
Demand scenario
        │
        ▼
Final demand instance
```

### Reproducibility and model comparison

Once generated, a demand instance is stored and reused as an experiment input.

The same instance is supplied to M1–M5:

``` text
                 Demand instance
                       │
           ┌─────┬─────┼─────┬─────┐
           │     │     │     │     │
           ▼     ▼     ▼     ▼     ▼
          M1    M2    M3    M4    M5
                 
```

All models therefore operate on identical:

- customer locations;
- customer identifiers;
- parcel assignments;
- instance size;
- demand scenario; and
- spatial demand realisation.

This common-demand design isolates the effect of the logistics configuration when comparing M1–M5.

Multiple demand seeds can subsequently be used to evaluate whether model performance remains consistent across different reproducible spatial customer realisations.

## Routing and OSRM Integration

OSRM provides the network-based distances and travel times required by the logistics simulation. The preparation and execution of the OSRM services are described in **Installation**; this section focuses on how those services are used internally by GLIMS.

The routing layer is primarily implemented under:

``` text
code/routing/
```

and acts as an interface between the logistics models and the external OSRM services.

At a high level:

``` text
Logistics model
      │
      ▼
Routing problem
      │
      ▼
GLIMS routing layer
      │
      ├──────────────► OSRM table queries
      │                    │
      │                    ▼
      │             Distance / duration
      │                  matrices
      │
      ▼
CWS / ILS optimisation
      │
      ▼
Ordered route plan
      │
      └──────────────► OSRM route queries
                           │
                           ▼
                    Route-level distance,
                    duration and geometry
```

This separation allows the optimisation algorithms to operate on network-based travel information without implementing the underlying road-network routing themselves.

------------------------------------------------------------------------

### Routing profiles

GLIMS distinguishes three OSRM routing profiles presented in **the OSRM Infrastructure** section.

These profiles are prepared independently because the accessible network, travel speeds, and routing restrictions differ by transport mode.

The profile required by the simulation depends on the distribution leg being evaluated.

| Model | Distribution leg          | Routing profile |
|:------|:--------------------------|:----------------|
| M1    | Direct delivery           | `driving`       |
| M2    | Direct delivery           | `driving`       |
| M3    | Facility supply           | `driving`       |
| M3    | Cargo-bike last mile      | `cycling`       |
| M4    | Facility supply           | `driving`       |
| M4    | Walking-courier last mile | `walking`       |
| M5    | Facility supply           | `driving`       |
| M5    | Customer collection       | `walking`       |

The distinction between vehicle type and routing profile is required for the correct representation of the models.

For example, M1 and M2 use different vehicle technologies, but both operate on the road network and therefore use the `driving` OSRM profile. Their operational, economic, and environmental characteristics are handled separately by the simulation model.

Similarly, the OSRM profile determines how movement is represented on the network; it does not by itself define vehicle capacity, emissions, costs, service times, or other logistics parameters.

------------------------------------------------------------------------

### Distance and Duration Matrices

Route optimisation requires repeated comparisons between many possible customer-to-customer movements.

Querying complete routes individually during every optimisation step would be unnecessarily expensive. GLIMS therefore uses OSRM matrix queries to obtain pairwise road-network information for the nodes involved in a routing problem.

Conceptually, for a set of locations:

``` text
Depot + Customers
        │
        ▼
OSRM table query
        │
        ├── Distance matrix
        │
        └── Duration matrix
```

A distance matrix represents:

$$
D =
\begin{bmatrix}
d_{00} & d_{01} & \cdots & d_{0n} \\
d_{10} & d_{11} & \cdots & d_{1n} \\
\vdots & \vdots & \ddots & \vdots \\
d_{n0} & d_{n1} & \cdots & d_{nn}
\end{bmatrix}
$$

where $d_{ij}$ is the road-network distance from location $i$ to location $j$.

An analogous matrix stores the corresponding travel durations.

These matrices provide the routing costs used by the route-construction and optimisation algorithms.

Because road networks may contain one-way streets and other directional restrictions, the cost from $i$ to $j$ does not necessarily have to be identical to the cost from $j$ to $i$.

------------------------------------------------------------------------

### Relationship with CWS and ILS

OSRM and the optimisation algorithms perform different tasks.

OSRM answers:

> How far apart are two locations over the applicable transport network, and how long does the movement take?

CWS and ILS answer:

> In what order should the customers be served while satisfying the routing constraints?

The interaction can therefore be represented as:

``` text
Customer coordinates
        │
        ▼
      OSRM
        │
        ▼
Network distance / duration matrices
        │
        ▼
      CWS
        │
        ▼
Initial feasible routing solution
        │
        ▼
 Optional ILS
        │
        ▼
Improved routing solution
```

The optimisation algorithms’ routing decisions are based on the network information obtained through OSRM.

The detailed behaviour and parameters of CWS and ILS are documented separately under **Routing Algorithms**.

------------------------------------------------------------------------

### Route Evaluation

Once the optimisation stage determines the ordered stops of a route, GLIMS can request route-level information from OSRM.

This allows the simulator to evaluate the actual sequence:

``` text
Depot
  │
  ▼
Customer 1
  │
  ▼
Customer 2
  │
  ▼
...
  │
  ▼
Depot
```

rather than treating the route only as a collection of pairwise matrix costs.

The resulting routing information is used to calculate the distance and duration associated with the corresponding distribution leg.

When route-geometry export is enabled, the road-network geometry returned by the routing process can also be stored with the experiment outputs.

This behaviour is controlled through the experiment configuration or the corresponding command-line option and is described under **Experiment Configuration** and **Outputs and Results**.

------------------------------------------------------------------------

### Routing Cache

Road-network queries represent a significant part of the computational cost of repeated experiments.

GLIMS therefore includes a routing-cache mechanism that allows previously calculated routing information to be reused when the same routing request appears again.

The cache is stored under the project cache structure rather than being treated as an experiment result.

Conceptually:

``` text
Routing request
      │
      ▼
Is result cached?
   ┌──┴──┐
  Yes    No
   │      │
   │      ▼
   │     OSRM
   │      │
   │      ▼
   │   Store result
   │      │
   └──┬───┘
      ▼
Return routing information
```

Caching is particularly useful when multiple experiments reuse the same:

- city;
- customer locations;
- facilities;
- transport profile; or
- demand instance.

It reduces repeated OSRM requests without changing the underlying routing information used by the simulation.

Cache behaviour can be controlled through the experiment configuration and is documented with the corresponding configuration parameters.

------------------------------------------------------------------------

### Routing Failures and Integrity

A valid geographic coordinate does not guarantee that OSRM can successfully connect that point to the requested transport network.

Potential routing problems include locations that cannot be matched appropriately to the selected network or origin-destination combinations for which a valid route cannot be obtained.

GLIMS therefore treats routing validity separately from logistics performance.

``` text
Routing request
      │
      ▼
Can OSRM resolve it?
   ┌──┴──┐
  Yes    No
   │      │
   ▼      ▼
Routing   Routing issue
solution      │
   │          │
   └────┬─────┘
        ▼
Integrity audit
```

The simulation records routing-related inconsistencies so that an experiment can be checked before its operational, economic, or environmental indicators are interpreted.

This is particularly important when comparing models that use different transport profiles, since a location reachable through the driving network is not necessarily represented identically in the cycling or walking networks.

The corresponding audit outputs are described under **Outputs and Results**.

------------------------------------------------------------------------

### Routing Profiles versus Logistics Parameters

OSRM should be understood as the **network-routing component** of GLIMS, not as the complete transport model.

For a given distribution leg, GLIMS combines:

``` text
OSRM
│
├── network accessibility
├── route distance
└── network travel information

        +

GLIMS logistics parameters
│
├── vehicle type
├── capacity
├── service time
├── operational constraints
├── costs
└── environmental factors

        │
        ▼
Simulated logistics operation
```

This separation allows different logistics technologies to share the same underlying road-network representation while retaining different operational and environmental characteristics.

For example:

``` text
M1: conventional van ─┐
                      ├──► driving network
M2: electric van ─────┘
```

Both models can therefore use the same road-network topology while remaining distinct logistics alternatives.

------------------------------------------------------------------------

### Routing Workflow Summary

The complete routing interaction inside a GLIMS experiment can be summarised as:

``` text
Customer / facility coordinates
             │
             ▼
       Select OSRM profile
             │
             ▼
      Check routing cache
             │
             ▼
       OSRM table service
             │
             ▼
 Distance + duration matrices
             │
             ▼
      CWS route construction
             │
             ▼
      Optional ILS improvement
             │
             ▼
       Ordered route plan
             │
             ▼
      Route-level evaluation
             │
             ▼
 Distance / duration / geometry
             │
             ▼
       Routing integrity
             │
             ▼
       Simulation outputs
```

This routing layer is shared across the logistics models while allowing each distribution leg to use the transport profile and operational parameters appropriate to its role.

## Experiment Configuration

GLIMS experiments are primarily defined through JSON configuration files stored under:

``` text
configs/experiments/
```

A configuration file provides a reproducible description of the conditions under which an experiment is executed. Rather than defining these settings manually for every run, the JSON file centralises the study area, demand instance, routing algorithm, facility behaviour, output settings, and other experiment-level options.

The general structure is:

``` text
Experiment configuration
│
├── Experiment identity and study area
├── Demand selection
├── OSRM profile
│
├── routing
│   ├── algorithm
│   ├── CWS behaviour
│   ├── ILS search parameters
│   ├── destruction / reconstruction
│   ├── relocate search
│   └── service-time constraints
│
├── traffic
│   ├── static traffic profile
│   ├── multiplier override
│   ├── time-dependent profile
│   └── simulation time
│
├── output
│   ├── route outputs
│   ├── metadata
│   ├── audit detail
│   └── performance information
│
├── facility_filter
│   └── facility candidate search
│
├── facility_assignment
│   └── facility capacity behaviour
│
└── osrm_cache
    └── routing-matrix caching
```

This section documents how these options are represented in an experiment configuration. Their underlying methodologies are described separately:

- demand construction → **Demand Generation**;
- network routing → **Routing and OSRM Integration**;
- CWS and ILS → **Routing Algorithms**;
- logistics-model behaviour → **Logistics Models**; and
- generated artifacts → **Outputs and Results**.

------------------------------------------------------------------------

### Core experiment settings

The top level of the JSON identifies the experiment and selects its main simulation inputs.

The principal fields are:

| Parameter         | Description                                                  |
|:------------------|:-------------------------------------------------------------|
| `experiment_name` | Human-readable identifier for the experiment.                |
| `city`            | City in which the experiment is executed.                    |
| `zones`           | Administrative zones included in the simulation.             |
| `demand_scenario` | Parcel-intensity scenario (`low`, `medium`, or `high`).      |
| `instance_size`   | Number of simulated customers.                               |
| `demand_seed`     | Seed or seeds selecting reproducible demand realisations.    |
| `osrm_profile`    | Primary OSRM routing profile associated with the experiment. |

The interpretation and generation of the demand inputs are described under **Demand Generation**.

For example, the combination:

``` json
{
  "city": "madrid",
  "zones": ["Embajadores"],
  "demand_scenario": "low",
  "instance_size": 40000,
  "demand_seed": 42
}
```

selects a Madrid experiment over the specified zone using the corresponding previously generated demand instance.

The simulator resolves the demand file from these settings unless an explicit demand-instance identifier is provided.

------------------------------------------------------------------------

### Routing Configuration

Routing and optimisation settings are grouped under:

``` json
"routing": {
    ...
}
```

The block determines whether routes are constructed directly with Clarke-Wright Savings (CWS) or further optimised through Iterated Local Search (ILS).

The current configuration includes parameters covering:

| Parameter                            | Purpose                                                                      |
|:-------------------------------------|:-----------------------------------------------------------------------------|
| `algorithm`                          | Selects `cws` or `ils`.                                                      |
| `cws_allow_route_reversal`           | Controls whether partial CWS routes may be reversed during endpoint merging. |
| `ils_max_iterations`                 | Maximum number of ILS iterations.                                            |
| `ils_max_no_improvement`             | Maximum search iterations without improvement before termination.            |
| `ils_destruction_percentage_step`    | Controls the increase in destruction intensity during ILS.                   |
| `ils_max_destruction_percentage`     | Limits the maximum destruction intensity.                                    |
| `ils_max_full_destruction_attempts`  | Limits repeated attempts at the maximum destruction level.                   |
| `ils_biased_cws_alpha_min`           | Lower bound of the biased-CWS parameter used during reconstruction.          |
| `ils_biased_cws_alpha_max`           | Upper bound of the biased-CWS parameter used during reconstruction.          |
| `ils_biased_cws_sampling_batch_size` | Controls the sampling batch used by the biased-CWS reconstruction mechanism. |
| `ils_restricted_relocate`            | Enables or disables the restricted relocate search.                          |
| `ils_relocate_candidate_fraction`    | Controls the fraction of relocate candidates considered.                     |
| `ils_relocate_neighbor_routes`       | Limits the neighbouring routes considered during relocate search.            |
| `ils_relocate_max_insertions`        | Limits insertion positions evaluated by the restricted relocate procedure.   |
| `ils_random_seed`                    | Seed or seeds controlling the stochastic behaviour of ILS.                   |
| `last_service_deadline_enabled`      | Enables or disables the last-service deadline constraint.                    |
| `last_service_margin_min`            | Defines the margin applied to the last-service deadline.                     |

These parameters are intentionally described only at configuration level here. Their algorithmic role and interaction are explained under **Routing Algorithms**.

When:

``` json
"algorithm": "cws"
```

the experiment uses the CWS routing procedure directly.

When:

``` json
"algorithm": "ils"
```

CWS contributes to the initial/reconstruction stages and the resulting solution is further processed by the ILS procedure.

------------------------------------------------------------------------

### Repeated Experiments and Random Seeds

GLIMS distinguishes between two independent sources of randomness:

``` text
demand_seed
    │
    └── controls the spatial demand realisation

ils_random_seed
    │
    └── controls stochastic behaviour of ILS
```

Both values may be defined either as a single integer or as a list of integers.

Before simulation, GLIMS expands these values into independent experiment replicates.

The expansion rules are:

| `demand_seed` | `ils_random_seed` | Result                                             |
|:--------------|:------------------|:---------------------------------------------------|
| Scalar        | Scalar            | One experiment                                     |
| Scalar        | List              | Fixed demand realisation with multiple ILS seeds   |
| List          | Scalar            | Multiple demand realisations with a fixed ILS seed |
| List          | List              | Seeds paired one-to-one by position                |

For example:

``` json
"demand_seed": [42, 101, 202],
"routing": {
    "ils_random_seed": [42, 101, 202]
}
```

produces:

``` text
Replicate 1 → demand_seed = 42  | ILS seed = 42
Replicate 2 → demand_seed = 101 | ILS seed = 101
Replicate 3 → demand_seed = 202 | ILS seed = 202
```

It does not produce the Cartesian product of all seed combinations.

Therefore, the example above creates three experiments rather than nine.

When both parameters are lists, their lengths must match.

This mechanism allows repeated experiments to be defined explicitly in a single configuration while preserving the distinction between variability in simulated demand and variability introduced by the optimisation algorithm.

------------------------------------------------------------------------

### Traffic Configuration

Traffic-related settings are grouped under:

``` json
"traffic": {
    ...
}
```

The block provides the temporal context of the simulation and the traffic profiles used during route construction and evaluation.

Its principal parameters are:

| Parameter                    | Description                                                                      |
|:-----------------------------|:---------------------------------------------------------------------------------|
| `static_profile`             | Selects the static traffic profile used during route construction.               |
| `static_multiplier_override` | Optionally overrides the multiplier associated with the selected static profile. |
| `time_profile`               | Selects the time-dependent traffic profile used for temporal route evaluation.   |
| `simulation_date`            | Date associated with the simulated operation.                                    |
| `shift_start`                | Starting time of the simulated delivery shift.                                   |
| `shift_duration_min`         | Duration of the simulated shift in minutes.                                      |

The date must follow:

``` text
YYYY-MM-DD
```

and the shift start:

``` text
HH:MM
```

Together:

``` text
simulation_date
       +
shift_start
       +
shift_duration_min
       │
       ▼
Simulated operating window
```

The simulator distinguishes between the traffic information used while constructing routing plans and the time-dependent profile used when evaluating those plans over the simulated shift.

> **Experimental status**
>
> The traffic-aware methodology is currently under development. These parameters are already integrated into the experiment interface so that traffic-related experiments can be conducted without changing the configuration structure, but traffic-aware results should currently be treated as experimental rather than part of the stable baseline methodology.

------------------------------------------------------------------------

### Output Configuration

Output behaviour is controlled through:

``` json
"output": {
    ...
}
```

This block determines which artifacts are persisted after an experiment and the level of detail included in them.

The principal options are:

| Parameter             | Description                                                           |
|:----------------------|:----------------------------------------------------------------------|
| `save_route_details`  | Enables detailed route-level outputs.                                 |
| `save_route_stops`    | Stores the ordered stops associated with generated routes.            |
| `save_route_geometry` | Stores OSRM route geometry when available.                            |
| `save_configuration`  | Stores the fully resolved experiment configuration.                   |
| `save_metadata`       | Stores experiment execution metadata.                                 |
| `summary_detail`      | Controls the detail included in summary exports.                      |
| `audit_detail`        | Controls the amount of routing/integrity audit information persisted. |
| `performance_profile` | Controls persistence/detail of performance-profiling information.     |
| `show_progress`       | Enables progress information during simulation.                       |

When configuration saving is enabled, the resolved configuration is stored with the experiment rather than merely copying the original JSON.

This is important when CLI overrides are used because the saved configuration records the values that were actually used during execution.

Similarly, metadata records experiment-level provenance and execution status.

The exact directory structure and generated files are described under **Outputs and Results**.

------------------------------------------------------------------------

### Facility Candidate Filtering

Facility candidate search is configured through:

``` json
"facility_filter": {
    ...
}
```

The available settings are:

| Parameter            | Description                                                                            |
|:---------------------|:---------------------------------------------------------------------------------------|
| `enabled`            | Enables or disables spatial candidate filtering.                                       |
| `initial_buffer_m`   | Initial search radius around the relevant simulation area, in metres.                  |
| `buffer_increment_m` | Distance by which the search area is expanded when additional candidates are required. |
| `maximum_buffer_m`   | Maximum search radius permitted.                                                       |
| `minimum_candidates` | Minimum number of candidate facilities sought before expansion stops.                  |

Conceptually:

``` text
Simulation area
      │
      ▼
Initial search buffer
      │
      ▼
Enough candidates?
   ┌──┴──┐
  Yes    No
   │      │
   │      ▼
   │   Expand buffer
   │      │
   │      ▼
   │   Check again
   │
   ▼
Candidate facilities
```

This mechanism allows facility-dependent models to search beyond the exact administrative boundary when insufficient candidate infrastructure is available immediately inside the selected zone.

The detailed use of these candidates by M3–M5 is described under **Logistics Models**.

------------------------------------------------------------------------

### Facility Assignment

Facility-capacity behaviour is configured through:

``` json
"facility_assignment": {
    ...
}
```

The current configuration distinguishes capacity behaviour for:

``` text
PUDO facilities
Microhubs
```

through:

| Parameter                | Description                                                               |
|:-------------------------|:--------------------------------------------------------------------------|
| `pudo_capacity_mode`     | Controls the capacity mode used when assigning demand to PUDO facilities. |
| `microhub_capacity_mode` | Controls the capacity mode used when assigning demand to microhubs.       |

These settings influence the facility-assignment stage rather than OSRM routing itself.

------------------------------------------------------------------------

### OSRM Matrix Cache

OSRM matrix caching is configured through:

``` json
"osrm_cache": {
    ...
}
```

The block controls whether previously computed OSRM matrix information can be reused.

Its main parameters are:

| Parameter   | Description                                                          |
|:------------|:---------------------------------------------------------------------|
| `enabled`   | Enables or disables the OSRM matrix cache.                           |
| `directory` | Defines the directory in which cached routing information is stored. |

Caching can substantially reduce repeated routing queries when experiments reuse the same locations and routing profile.

The cache affects computational performance, not the logistics methodology itself.

Its role in the routing architecture is described under **Routing and OSRM Integration**.

------------------------------------------------------------------------

### Configuration and CLI Overrides

An experiment configuration is passed to the simulator with:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json
```

On Windows PowerShell:

``` powershell
python -m code.simulation.osrm_simulator --config .\configs\experiments\<experiment>.json
```

The JSON acts as the base experiment definition.

Selected parameters can then be replaced temporarily through command-line arguments:

``` text
JSON configuration
       │
       ▼
Base experiment
       │
       + CLI overrides
       │
       ▼
Resolved configuration
       │
       ▼
Simulation
```

For example:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/madrid_embajadores_ils_baseline.json \
  --demand-seed 101
```

uses the selected JSON configuration but replaces its demand seed for that execution.

The simulator currently exposes CLI overrides for:

``` text
--city
--zones

--demand-scenario
--instance-size
--demand-seed
--demand-instance-id

--profile

--routing-algorithm
--cws-allow-route-reversal

--ils-max-iterations
--ils-max-no-improvement
--ils-perturbation-moves
--ils-biased-cws-alpha-min
--ils-biased-cws-alpha-max
--ils-restricted-relocate
--no-ils-restricted-relocate
--ils-relocate-candidate-fraction
--ils-relocate-neighbor-routes
--ils-relocate-max-insertions
--ils-random-seed

--save-route-geometry

--traffic-profile
--traffic-multiplier
--simulation-date
--shift-start
--shift-duration-min
--time-traffic-profile
```

The authoritative CLI reference for the installed version can always be inspected with:

``` bash
python -m code.simulation.osrm_simulator --help
```

Not every JSON setting necessarily has a corresponding CLI override.

For this reason, configuration files should be considered the primary definition of reproducible experiments, while CLI arguments are best used for temporary changes and individual tests.

------------------------------------------------------------------------

### Creating a New Experiment

New experiment configurations should normally be created from an existing configuration with similar methodological settings.

A practical workflow is:

``` text
Existing experiment
        │
        ▼
Copy JSON
        │
        ▼
Rename experiment
        │
        ▼
Change only the variables
being investigated
        │
        ▼
Run experiment
        │
        ▼
Resolved configuration +
metadata + results
```

Experiment filenames should make the main experimental distinction identifiable whenever possible.

For example:

``` text
configs/experiments/
├── madrid_embajadores_ils_baseline.json
├── madrid_embajadores_ils_experiment_01.json
└── madrid_embajadores_ils_experiment_02.json
```

A configuration can then be executed directly:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/madrid_embajadores_ils_baseline.json
```

or on Windows:

``` powershell
python -m code.simulation.osrm_simulator --config .\configs\experiments\madrid_embajadores_ils_baseline.json
```

------------------------------------------------------------------------

### Reproducible Experiment Design

When the objective is to compare logistics models, only the experimental dimensions under investigation should change.

For example:

``` text
Same city
      +
Same zones
      +
Same demand instance
      +
Same routing assumptions
      +
Same facility assumptions
      │
      ▼
Change logistics model
      │
      ▼
Comparable model results
```

The distinction between the demand seed and optimisation seed should also be preserved:

``` text
Demand variability
      │
      └── demand_seed

Optimisation variability
      │
      └── ils_random_seed
```

A reproducible GLIMS experiment is therefore associated with the combination of:

``` text
Experiment configuration
        +
Input datasets
        +
Demand instance
        +
Demand seed
        +
Routing algorithm
        +
Optimisation seed
        +
Facility assumptions
        +
Traffic assumptions
```

When enabled, GLIMS stores the resolved experiment configuration and execution metadata alongside the results, allowing the conditions used for a particular run to be recovered later.

## Routing Algorithms

GLIMS currently provides two routing strategies for constructing the vehicle routes required by the logistics models:

- **Clarke-Wright Savings (CWS)**, used as a constructive routing heuristic; and
- **Iterated Local Search (ILS)**, which builds upon CWS and applies additional local-search and destroy-and-reconstruct procedures.

Both algorithms operate on the network-based distance and duration matrices obtained through the routing layer described under **Routing and OSRM Integration**.

Their role can be summarised as:

``` text
Customer / depot locations
          │
          ▼
OSRM distance and duration matrices
          │
          ▼
     Routing algorithm
       ┌──┴──┐
       │     │
      CWS   ILS
       │     │
       │     └── CWS initial solution
       │          +
       │       Local search
       │          +
       │    Destroy / reconstruct
       ├──────────┘
       ▼
Feasible customer routes
          │
          ▼
Logistics-model evaluation
```

The routing algorithms determine the sequence and grouping of customers into routes. OSRM remains responsible for providing the underlying network travel information.

------------------------------------------------------------------------

### Routing Objective and Feasibility

A routing solution consists of one or more routes, where each route represents one vehicle leaving the depot, serving a sequence of customers, and returning to the depot.

Conceptually:

``` text
Route 1: Depot → C1 → C4 → C7 → Depot
Route 2: Depot → C2 → C3 → C6 → Depot
Route 3: Depot → C5 → C8 → Depot
```

The depot is represented internally by matrix index `0`, while customers occupy indices `1 ... n`.

The routing objective used by CWS and ILS is to minimise the total network distance of the resulting set of routes:

$$
\min \sum_{r \in R} d(r)
$$

where $R$ is the set of routes and $d(r)$ is the total distance of route $r$, including travel from and back to the depot.

Candidate solutions must also remain feasible regarding the operational constraints supplied to the routing algorithm.

The principal routing constraints are:

``` text
Customer assignment
        +
Vehicle capacity
        +
Route-duration limit
        │
        ▼
Feasible route
```

Each customer must belong to a route, and the sum of customer demand assigned to a vehicle cannot exceed its configured capacity.

When a duration limit is active, the complete:

``` text
Depot → customers → Depot
```

tour must also fit within the permitted route duration, including the service time associated with customer stops.

These feasibility conditions are preserved during route construction and subsequent improvement procedures.

------------------------------------------------------------------------

### Clarke-Wright Savings

The Clark–Wright Savings algorithm is the constructive routing heuristic used by GLIMS.

CWS starts from the most fragmented feasible solution possible:

``` text
Depot → C1 → Depot
Depot → C2 → Depot
Depot → C3 → Depot
Depot → C4 → Depot
...
```

In other words, every customer initially has an independent route.

The algorithm then progressively merges routes whenever joining two customers produces a useful distance saving and the resulting route remains feasible.

------------------------------------------------------------------------

#### Savings calculation

Consider two customers $i$ and $j$.

Serving them independently requires the connections:

``` text
Depot → ... → i → Depot

Depot → j → ... → Depot
```

Joining the routes removes:

``` text
i → Depot
Depot → j
```

and replaces them with:

``` text
i → j
```

GLIMS therefore calculates the directed saving:

$$
s_{ij} = d_{i0} + d_{0j} - d_{ij}
$$

where:

- $d_{i0}$ is the distance from customer $i$ to the depot;
- $d_{0j}$ is the distance from the depot to customer $j$; and
- $d_{ij}$ is the distance from customer $i$ to customer $j$.

Savings are calculated for every valid ordered pair:

$$
i \neq j
$$

and sorted from highest to lowest.

The formulation is deliberately directed.

Because GLIMS obtains its routing matrices from a road network, it cannot assume:

$$
d_{ij} = d_{ji}
$$

One-way streets, turn restrictions, and other network characteristics may make travel asymmetric.

Consequently:

$$
s_{ij}
$$

and:

$$
s_{ji}
$$

may also differ.

------------------------------------------------------------------------

#### Route merging

After sorting the savings, CWS examines the candidate merges from largest to smallest.

For a merge:

``` text
Route A → ... → i

j → ... → Route B
```

to be accepted, the corresponding customers must occur at compatible route endpoints.

The basic merge is therefore:

``` text
[ ... i ] + [ j ... ]

             ▼

[ ... i → j ... ]
```

A merge is rejected if the two customers already belong to the same route.

It is also rejected if the resulting vehicle load exceeds:

$$
Q_r \leq Q_{\max}
$$

where $Q_r$ is the total demand assigned to the merged route and $Q_{\max}$ is vehicle capacity.

When a maximum route duration is active, GLIMS additionally evaluates the complete merged route and rejects the merge when:

$$
T_r > T_{\max}
$$

The process continues through the ordered savings list until no additional candidate merge can be applied.

The resulting routes form the final CWS solution.

------------------------------------------------------------------------

#### Route reversal

CWS can optionally allow partial routes to be reversed before endpoint merging.

Without route reversal, a directed merge requires the selected customer $i$ to occur at the end of its current route and customer $j$ at the beginning of the other route:

``` text
[ ... → i ] + [ j → ... ]
```

Allowing reversal increases the number of possible endpoint configurations that can be considered during construction.

This behaviour is controlled by:

``` text
cws_allow_route_reversal
```

Because OSRM matrices may be asymmetric, reversing a route is not necessarily cost-neutral. The reversed sequence must therefore be evaluated according to its resulting directed network cost and feasibility.

------------------------------------------------------------------------

#### CWS summary

The complete constructive process can be represented as:

``` text
One route per customer
        │
        ▼
Calculate directed savings
        │
        ▼
Sort savings descending
        │
        ▼
Select next candidate (i, j)
        │
        ▼
Different routes?
        │
        ▼
Compatible endpoints?
        │
        ▼
Capacity feasible?
        │
        ▼
Duration feasible?
        │
        ▼
Merge routes
        │
        └───────────────┐
                        │
               Continue savings list
                        │
                        ▼
                   Final routes
```

CWS therefore provides a fast constructive solution and can be used either as the final routing algorithm or as the starting point for ILS.

------------------------------------------------------------------------

### Iterated Local Search

Iterated Local Search extends the CWS solution by repeatedly exploring alternative route configurations.

The implementation used by GLIMS combines four main components:

``` text
CWS construction
       │
       ▼
Local search
       │
       ▼
Destroy routes
       │
       ▼
Biased-randomized CWS reconstruction
       │
       ▼
Local search
       │
       ▼
Acceptance decision
       │
       └────────► repeat
```

The objective is to escape the local structure produced by the deterministic CWS construction while preserving capacity and route-duration feasibility.

------------------------------------------------------------------------

#### Initial solution

ILS does not begin from a random routing solution.

First, GLIMS executes the standard CWS algorithm:

``` text
Customers
    │
    ▼
Standard CWS
    │
    ▼
Initial feasible routes
```

The resulting solution is then immediately passed through local search.

This produces the initial base solution from which the iterative search begins.

------------------------------------------------------------------------

### Local Search

The local-search stage contains an intra-route improvement procedure and, when enabled, an inter-route improvement procedure:

``` text
Current routes
      │
      ▼
Intra-route 2-opt
      │
      ▼
Restricted inter-route relocate
      │
      ▼
Locally improved solution
```

------------------------------------------------------------------------

#### Intra-route 2-opt

GLIMS applies 2-opt independently to every route.

Given a route:

``` text
Depot → A → B → C → D → Depot
```

2-opt considers reversing subsequences of customers.

For example:

``` text
A → B → C → D

       ▼

A → C → B → D
```

Each candidate is evaluated using the directed routing matrix.

A candidate reversal is accepted only when it reduces route distance and, when a duration constraint is active, the modified route remains duration-feasible.

The implementation uses a best-improvement strategy: all considered reversals for the current route are evaluated, the best improving candidate is selected, and the process repeats until no further 2-opt improvement can be found.

Because the underlying matrix may be asymmetric, reversing a customer sequence changes all affected directed travel costs and is evaluated explicitly.

------------------------------------------------------------------------

### Restricted Inter-Route Relocate

After 2-opt, GLIMS can optionally perform a restricted relocate search between routes.

A relocate operation removes a customer from one route and inserts it into another:

``` text
Before

Route A: Depot → A → X → B → Depot
Route B: Depot → C → D → Depot


After

Route A: Depot → A → B → Depot
Route B: Depot → C → X → D → Depot
```

An exhaustive relocate search can become expensive for large routing instances.

GLIMS therefore restricts the search to a subset of promising moves.

------------------------------------------------------------------------

#### Candidate customer selection

For each customer, the algorithm estimates the distance that would be saved by removing it from its current route.

For customer $i$, with predecessor $p$ and successor $s$:

$$
g_i = d_{pi} + d_{is} - d_{ps}
$$

Customers with the largest removal gains have the greatest marginal contribution to the current route distance.

They are ranked by this value, and only the configured top fraction is considered.

This fraction is controlled by:

``` text
ils_relocate_candidate_fraction
```

For example:

``` text
0.10
```

means that only the top 10% of customers according to removal gain are considered as relocate candidates.

------------------------------------------------------------------------

#### Candidate destination routes

The algorithm does not test every candidate customer against every other route.

Instead, it estimates the proximity between the customer and each possible destination route.

Because the routing matrix may be asymmetric, proximity considers both directions between the customer and customers already belonging to the destination route.

Only the closest configured number of routes is retained:

``` text
ils_relocate_neighbor_routes
```

This substantially reduces the relocate search space.

------------------------------------------------------------------------

#### Candidate insertion positions

Within each selected destination route, GLIMS computes the additional distance caused by inserting the customer at each possible position.

For an insertion between customers $p$ and $s$:

$$
\Delta = d_{pi} + d_{is} - d_{ps}
$$

The insertion positions are ranked by this additional cost.

Only the best:

``` text
ils_relocate_max_insertions
```

positions are evaluated in detail.

The move must:

- reduce total route distance;
- preserve destination vehicle capacity;
- preserve the source-route duration constraint;
- preserve the destination-route duration constraint.

Improving feasible relocate moves are accepted immediately.

A relocate operation may also remove the final customer from a source route. In that case, the empty route disappears from the solution.

------------------------------------------------------------------------

### Destruction

After local search, ILS attempts to escape the current solution by partially destroying it.

The current GLIMS implementation destroys complete routes, rather than independently selecting individual customers.

For example:

``` text
Current solution

R1 ── C1 C4 C8
R2 ── C2 C5
R3 ── C3 C7 C9
R4 ── C6 C10

Destroy 50% of routes

R1 ── retained
R2 ── destroyed ──┐
R3 ── retained    │
R4 ── destroyed ──┤
                  ▼
           Freed customers
          C2 C5 C6 C10
```

Routes are selected randomly using the ILS random generator.

The number of routes destroyed is determined from the current destruction percentage, while ensuring that at least one route is selected.

This produces:

``` text
Remaining routes
        +
Freed customers
```

The remaining routes are preserved unchanged during reconstruction.

------------------------------------------------------------------------

### Biased-Randomized CWS Reconstruction

The customers released by destruction must be reinserted into a feasible routing structure.

Instead of simply executing the deterministic CWS algorithm again, GLIMS uses a biased-randomized CWS (BR-CWS) procedure.

A routing subproblem is constructed containing:

``` text
Depot
  +
Freed customers
```

together with their corresponding:

- distance matrix;
- duration matrix; and
- customer demands.

CWS savings are then generated for this subproblem.

However, reconstruction does not always process the savings list strictly from the highest saving to the lowest.

Instead, GLIMS uses a biased-randomized candidate-selection mechanism. A restricted window containing the highest-ranked remaining savings is maintained, and candidate ranks are sampled from a geometric distribution.

For each selection step, the geometric-distribution parameter is sampled as:

$$
\alpha \sim U(\alpha_{\min}, \alpha_{\max})
$$

where the bounds are configured through:

``` text
ils_biased_cws_alpha_min
ils_biased_cws_alpha_max
```

The resulting selection strongly favours high-ranked savings while retaining a non-zero probability of exploring lower-ranked alternatives.

As candidates are consumed, the restricted candidate window advances through the sorted savings list. The selected merge is still accepted only if all standard CWS feasibility conditions are satisfied.

This introduces controlled diversification without replacing the underlying CWS feasibility logic.

This gives ILS a way to explore routing configurations that deterministic CWS might never produce.

After reconstruction:

``` text
Retained routes
       +
BR-CWS reconstructed routes
       │
       ▼
Candidate solution
```

The candidate is subsequently passed through local search again.

------------------------------------------------------------------------

### Adaptive Destruction Intensity

ILS does not necessarily destroy the same proportion of routes at every iteration.

The destruction percentage grows when successive attempts fail to improve the current solution.

Conceptually:

``` text
Small destruction
       │
       ▼
Improvement?
 ┌─────┴─────┐
Yes          No
 │            │
 ▼            ▼
Reset      Increase
destruction destruction
 │            │
 └──────┬─────┘
        ▼
  Next iteration
```

The increase is controlled through:

``` text
ils_destruction_percentage_step
```

and cannot exceed:

``` text
ils_max_destruction_percentage
```

This creates an adaptive diversification mechanism.

Initially, ILS explores modifications close to the current routing structure. If those modifications repeatedly fail, increasingly large parts of the solution can be rebuilt.

After an improvement, the destruction level is reset.

------------------------------------------------------------------------

### Candidate Acceptance

GLIMS uses a strict improving acceptance criterion.

Let:

$$
C_{\text{current}}
$$

be the cost of the current base solution and:

$$
C_{\text{candidate}}
$$

the cost after destruction, reconstruction, and local search.

The candidate replaces the current solution only when:

$$
C_{\text{candidate}} < C_{\text{current}}
$$

If the candidate does not improve the current solution:

``` text
Candidate rejected
        │
        ▼
Current solution retained
        │
        ▼
Destruction intensity increases
```

If it improves the solution:

``` text
Candidate accepted
        │
        ▼
Becomes new current solution
        │
        ▼
Destruction intensity reset
```

GLIMS also keeps track of the best solution encountered during the search.

This means ILS does not accept temporary worsening moves. Diversification is instead introduced through route destruction and randomized reconstruction.

------------------------------------------------------------------------

### Stopping Criteria

The ILS search is bounded by two principal stopping conditions.

#### Maximum iterations

``` text
ils_max_iterations
```

defines the absolute maximum number of ILS iterations.

#### Iterations without improvement

``` text
ils_max_no_improvement
```

limits how many consecutive unsuccessful iterations may occur.

The search therefore stops when either:

``` text
Maximum iterations reached
             OR
Maximum consecutive iterations
without improvement reached
```

The second criterion prevents the algorithm from continuing indefinitely when additional destroy-and-reconstruct attempts are no longer producing better solutions.

------------------------------------------------------------------------

### Randomness and Reproducibility

The stochastic components of ILS use:

``` text
ils_random_seed
```

to initialise the algorithm’s random-number generator.

The seed affects operations such as:

- selection of routes for destruction; and
- biased-randomized CWS reconstruction.

It does not control customer generation.

As described under **Experiment Configuration**:

``` text
demand_seed
    │
    └── customer/demand realisation

ils_random_seed
    │
    └── optimisation realisation
```

Keeping these seeds separate allows GLIMS to distinguish variability caused by the simulated demand from variability introduced by the optimisation algorithm.

------------------------------------------------------------------------

### Capacity and Duration Feasibility

Both CWS and ILS preserve the operational feasibility conditions supplied by the logistics model.

#### Vehicle capacity

For every route $r$:

$$
\sum_{i \in r} q_i \leq Q
$$

where:

- $q_i$ is the demand of customer $i$; and
- $Q$ is the corresponding vehicle capacity.

A customer whose individual demand already exceeds vehicle capacity makes the routing instance infeasible.

#### Route duration

When a maximum duration is configured, GLIMS evaluates the complete route:

``` text
Route start
    +
Depot → first customer
    +
Inter-customer travel
    +
Service times
    +
Last customer → depot
```

against the permitted limit.

Therefore:

$$
T_r \leq T_{\max}
$$

must hold for every route.

Before CWS construction begins, GLIMS also checks whether each individual customer could be served by an independent:

``` text
Depot → Customer → Depot
```

route within the configured duration limit.

If even this individual route is infeasible, the routing problem cannot be solved under the supplied duration constraint.

The same duration-feasibility checks are applied when ILS modifies routes through 2-opt, relocate, or reconstruction.

------------------------------------------------------------------------

### CWS versus ILS

The two routing options therefore represent different trade-offs between computational effort and solution improvement.

| Characteristic                   | CWS   | ILS      |
|:---------------------------------|:------|:---------|
| Constructive routing             | Yes   | Uses CWS |
| Deterministic base construction  | Yes   | Yes      |
| Intra-route 2-opt                | No    | Yes      |
| Inter-route relocate             | No    | Optional |
| Destroy/reconstruct              | No    | Yes      |
| Biased-randomized reconstruction | No    | Yes      |
| Stochastic component             | No\*  | Yes      |
| Capacity constraints             | Yes   | Yes      |
| Route-duration constraints       | Yes   | Yes      |
| Computational effort             | Lower | Higher   |

\* Standard CWS is deterministic for a fixed routing matrix, demand, configuration, and tie ordering. Biased-randomized CWS is used inside the ILS reconstruction stage.

CWS is therefore useful both as a standalone routing heuristic and as a reference solution.

ILS attempts to improve upon this baseline by combining:

``` text
CWS
 +
2-opt
 +
restricted relocate
 +
adaptive destruction
 +
biased-randomized reconstruction
```

at the cost of additional computation.

------------------------------------------------------------------------

### Complete ILS Workflow

The implementation can be summarised as:

``` text
OSRM distance / duration matrices
              │
              ▼
        Standard CWS
              │
              ▼
            2-opt
              │
              ▼
     Restricted relocate
         (if enabled)
              │
              ▼
       Initial solution
              │
              ▼
      ┌── ILS iteration ──────────────────────┐
      │                                       │
      │   Select routes for destruction       │
      │               │                       │
      │               ▼                       │
      │       Release their customers         │
      │               │                       │
      │               ▼                       │
      │       BR-CWS reconstruction           │
      │               │                       │
      │               ▼                       │
      │             2-opt                     │
      │               │                       │
      │               ▼                       │
      │      Restricted relocate              │
      │          (if enabled)                 │
      │               │                       │
      │               ▼                       │
      │       Candidate solution              │
      │               │                       │
      │               ▼                       │
      │        Strict improvement?            │
      │           ┌───┴───┐                   │
      │          Yes      No                  │
      │           │        │                  │
      │           ▼        ▼                  │
      │        Accept    Reject               │
      │           │        │                  │
      │           ▼        ▼                  │
      │         Reset    Increase             │
      │       destruction destruction         │
      │           │        │                  │
      └───────────┴────────┴──────────────────┘
                      │
                      ▼
              Stopping criterion
                      │
                      ▼
              Best routing solution
```

The resulting routes are subsequently evaluated by the corresponding logistics model and written to the experiment outputs as described under **Outputs and Results**.

## Running GLIMS

Once the required datasets, demand instances, OSRM services, and experiment configuration are available, GLIMS experiments are executed through the main simulation entry point:

``` text
code/simulation/osrm_simulator.py
```

The simulator should normally be launched from the root directory of the repository using the module interface:

``` bash
python -m code.simulation.osrm_simulator
```

The general execution workflow is:

``` text
Prepared city data
        +
Generated demand instance
        +
Running OSRM services
        +
Experiment configuration
        │
        ▼
python -m code.simulation.osrm_simulator
        │
        ▼
Load and resolve experiment settings
        │
        ▼
Prepare study area and facilities
        │
        ▼
Load selected demand instance
        │
        ▼
Construct routing problems
        │
        ▼
      CWS / ILS
        │
        ▼
      M1–M5
        │
        ▼
Routing integrity checks
        │
        ▼
Results + metadata
```

The methodological meaning of these stages is described in the preceding sections. This section focuses on how experiments are executed in practice.

------------------------------------------------------------------------

### Before Running an Experiment

A standard GLIMS experiment requires four main components to be available:

``` text
1. Python environment
2. Required input datasets
3. Generated demand instance
4. Running OSRM services
```

The Python virtual environment should first be activated.

**Windows — PowerShell**

``` powershell
.\.venv\Scripts\Activate.ps1
```

**Linux**

``` bash
source .venv/bin/activate
```

The terminal should normally display:

``` text
(.venv)
```

when the environment is active.

The required city-level datasets and classified logistics facilities must also have been prepared according to the **Data and Processing Pipeline** section.

The selected demand instance must exist under:

``` text
results/<city>/demand/
```

For example:

``` text
results/madrid/demand/demand_low_40000_seed_42.csv
```

Finally, the required OSRM services must be running.

They can be checked with:

``` bash
docker ps
```

A complete experiment may use more than one transport profile because M1–M5 contain different distribution legs. Therefore, the `driving`, `cycling`, and `walking` services prepared for the selected city should normally be available before executing the complete model comparison.

------------------------------------------------------------------------

### Checking the Simulator Interface

The currently supported command-line options can be inspected with:

``` bash
python -m code.simulation.osrm_simulator --help
```

The interface currently exposes options related to:

- experiment configuration;
- city and study zones;
- demand scenario and instance selection;
- OSRM routing profile;
- CWS and ILS;
- ILS search parameters and random seed;
- route-geometry export;
- traffic-related experimental settings;
- simulation date and shift definition.

------------------------------------------------------------------------

### Running an Experiment from a Configuration File

The recommended way to execute a reproducible GLIMS experiment is:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json
```

For example:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/madrid_embajadores_ils_baseline.json
```

On PowerShell, the same command can be written as:

``` powershell
python -m code.simulation.osrm_simulator `
  --config .\configs\experiments\madrid_embajadores_ils_baseline.json
```

or simply:

``` powershell
python -m code.simulation.osrm_simulator --config .\configs\experiments\madrid_embajadores_ils_baseline.json
```

When a configuration file is supplied, it defines the main experimental conditions described under **Experiment Configuration**, including the study area, demand selection, routing method, facility behaviour, output settings, and other simulation parameters.

Conceptually:

``` text
configs/experiments/<experiment>.json
                 │
                 ▼
          osrm_simulator.py
                 │
                 ▼
      Resolve experiment settings
                 │
                 ▼
          Execute experiment
```

Keeping these settings in configuration files is preferable to maintaining long experiment-specific commands because the complete experimental definition can be stored under version control and reused later.

------------------------------------------------------------------------

### Running with Command-Line Overrides

Selected experiment settings can also be supplied through command-line arguments.

This makes it possible to reuse an existing configuration while temporarily changing a specific experimental condition.

For example, an experiment configured to use ILS can be executed with CWS instead using:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/madrid_embajadores_ils_baseline.json \
  --routing-algorithm cws
```

Similarly, the city can be overridden with:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --city madrid
```

and the simulation zones with:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --zones Embajadores
```

Multiple zones can be supplied after `--zones` when required.

Other currently exposed overrides include parameters such as:

``` text
--demand-scenario
--instance-size
--demand-seed
--demand-instance-id
--profile
--routing-algorithm
--cws-allow-route-reversal
--ils-max-iterations
--ils-max-no-improvement
--ils-perturbation-moves
--ils-biased-cws-alpha-min
--ils-biased-cws-alpha-max
--ils-restricted-relocate
--no-ils-restricted-relocate
--ils-relocate-candidate-fraction
--ils-relocate-neighbor-routes
--ils-relocate-max-insertions
--ils-random-seed
--save-route-geometry
--traffic-profile
--traffic-multiplier
--simulation-date
--shift-start
--shift-duration-min
--time-traffic-profile
```

The authoritative list for the installed version of GLIMS should always be obtained with:

``` bash
python -m code.simulation.osrm_simulator --help
```

This avoids duplicating the complete parameter documentation already provided under **Experiment Configuration** while keeping the execution interface discoverable.

------------------------------------------------------------------------

### Selecting the Demand Instance

Demand generation and logistics simulation are intentionally separate stages.

An experiment therefore consumes a previously generated demand instance rather than generating a new customer population during execution.

The usual selection is based on:

``` text
city
+
demand_scenario
+
instance_size
+
demand_seed
```

which corresponds to a file following the convention:

``` text
demand_<scenario>_<size>_seed_<seed>.csv
```

For example:

``` text
city            = madrid
demand_scenario = low
instance_size   = 40000
demand_seed     = 42
```

These values are normally defined in the experiment configuration.

They can also be overridden from the command line:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --demand-scenario low \
  --instance-size 40000 \
  --demand-seed 42
```

An explicit demand instance can instead be selected using:

``` text
--demand-instance-id
```

This option accepts the corresponding demand CSV filename or stem inside:

``` text
results/<city>/demand/
```

Reusing the same demand instance is important when comparing alternative logistics or routing configurations because it prevents changes in customer locations or parcel assignments from being introduced simultaneously with the experimental change being studied.

------------------------------------------------------------------------

### Selecting CWS or ILS

The routing strategy is normally defined by the `routing.algorithm` setting in the experiment configuration.

The two currently supported values are:

``` text
cws
ils
```

A routing method can also be selected explicitly from the command line.

#### CWS

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --routing-algorithm cws
```

In this case, Clarke-Wright Savings constructs the routing solution directly.

#### ILS

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --routing-algorithm ils
```

ILS uses the CWS-based construction and subsequently applies the improvement procedures described under **Routing Algorithms**.

Individual ILS settings can also be overridden.

For example:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --routing-algorithm ils \
  --ils-max-iterations 500 \
  --ils-max-no-improvement 100 \
  --ils-random-seed 42
```

------------------------------------------------------------------------

### Running on Windows and Linux

The Python entry point is platform-independent.

The main difference between Windows PowerShell and Linux shells is the syntax used to continue a command across multiple lines.

#### Windows — PowerShell

PowerShell uses the backtick:

``` powershell
python -m code.simulation.osrm_simulator `
  --config .\configs\experiments\madrid_embajadores_ils_baseline.json
```

#### Linux

Linux shells such as Bash use the backslash:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/madrid_embajadores_ils_baseline.json
```

Both can also execute the command on a single line:

``` bash
python -m code.simulation.osrm_simulator --config configs/experiments/madrid_embajadores_ils_baseline.json
```

Repository-relative paths are recommended so that experiment commands remain portable between machines.

------------------------------------------------------------------------

### Running Multiple Experiments Sequentially

Large experimental campaigns commonly require several configuration files to be executed one after another.

On Linux, multiple experiments can be chained with `&&`:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/experiment_01.json && \
python -m code.simulation.osrm_simulator \
  --config configs/experiments/experiment_02.json && \
python -m code.simulation.osrm_simulator \
  --config configs/experiments/experiment_03.json
```

Using `&&` means that the next experiment starts only when the preceding command terminates successfully.

This behaviour is useful for controlled experiment batches because an execution failure prevents the remaining chain from continuing silently.

For a larger collection of experiments, a Bash loop can be used:

``` bash
for config in \
  configs/experiments/experiment_01.json \
  configs/experiments/experiment_02.json \
  configs/experiments/experiment_03.json
do
    echo "Running $config"
    python -m code.simulation.osrm_simulator --config "$config" || break
done
```

The `|| break` condition stops the sequence if one experiment returns an error.

A similar PowerShell workflow can be written as:

``` powershell
$configs = @(
    ".\configs\experiments\experiment_01.json",
    ".\configs\experiments\experiment_02.json",
    ".\configs\experiments\experiment_03.json"
)

foreach ($config in $configs) {
    Write-Host "Running $config"

    python -m code.simulation.osrm_simulator --config $config

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Experiment failed. Stopping batch."
        break
    }
}
```

For systematic experimental campaigns, maintaining separate JSON configurations is preferable to embedding large numbers of parameter combinations directly into shell commands.

------------------------------------------------------------------------

### Experiment Completion and Failure

An experiment should not be considered valid solely because the simulator produced result files.

The complete execution must also be evaluated through the routing-integrity and experiment-status information generated by GLIMS.

Conceptually:

``` text
Simulation finished
        │
        ▼
Were outputs generated?
        │
        ▼
Did the experiment complete successfully?
        │
        ▼
Do routing-integrity checks pass?
        │
        ▼
Are expected customers / packages represented?
        │
        ▼
Results can be interpreted
```

The generated validation and audit artifacts are described in **Outputs and Results**.

------------------------------------------------------------------------

## Outputs and Results

Each GLIMS experiment produces a structured output directory containing the main model results together with routing diagnostics, facility information, execution metadata, and validation artifacts.

The output structure is designed to support three complementary purposes:

``` text
Experiment outputs
        │
        ├── Model results
        │   ├── summary.csv
        │   ├── routes/
        │   └── facilities/
        │
        ├── Routing diagnostics
        │   └── routing/
        │
        └── Validation and reproducibility
            ├── config.json
            ├── metadata.json
            ├── neighborhood_status.csv
            └── audit/
```

The main `summary.csv` file therefore represents only one level of the experiment output. Detailed routing, facility, optimisation, and integrity information is stored separately so that model comparison and execution validation remain distinguishable.

------------------------------------------------------------------------

### Experiment Output Directory

Each simulation creates an experiment-specific directory containing both aggregated results and, when multiple zones are evaluated, zone-level outputs.

A typical structure is:

``` text
<experiment_id>/
│
├── config.json
├── metadata.json
├── neighborhood_status.csv
├── summary.csv
│
├── routes/
│   ├── m1_routes.csv
│   ├── m2_routes.csv
│   ├── m3_routes.csv
│   ├── m4_routes.csv
│   ├── m5_routes.csv
│   └── route_stops.csv
│
├── routing/
│   └── routing_plan_metrics.csv
│
├── facilities/
│   └── facility_summary.csv
│
├── audit/
│   ├── routing_integrity_summary.csv
│   ├── route_customer_summary.csv
│   ├── unroutable_customers.csv
│   └── performance_profile.csv
│
└── neighborhoods/
    ├── <zone_1>/
    │   ├── metadata.json
    │   ├── summary.csv
    │   ├── routes/
    │   ├── routing/
    │   ├── facilities/
    │   └── audit/
    │
    ├── <zone_2>/
    │   └── ...
    │
    └── ...
```

The exact set of generated files depends on the options enabled under the `output` block of the experiment configuration.

For experiments involving several neighborhoods or administrative zones, GLIMS preserves the detailed results associated with each zone while also producing experiment-level outputs.

This separation makes it possible to analyse results at different levels:

``` text
Experiment
    │
    ├── Overall results
    │
    └── Neighborhoods
        ├── Zone 1
        ├── Zone 2
        └── ...
```

------------------------------------------------------------------------

### Summary Results

The principal output for comparing the logistics models is:

``` text
summary.csv
```

This file contains the aggregated indicators generated for the evaluated models.

Conceptually:

``` text
                         summary.csv
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
       Operational       Environmental       Economic
        indicators        indicators         indicators
```

Depending on the evaluated model and available outputs, the summary includes indicators such as:

``` text
packages served
distance travelled
number of trips
CO₂ emissions
NOx emissions
route operating costs
facility service costs
other model-specific costs
total costs
cost per package
```

Representative fields include:

``` text
paquetes
km_recorridos
numero_viajes

emisiones_co2_kg
emisiones_nox_kg

costo_operacion_ruta_eur
costo_servicio_facility_eur
otros_costos_eur
costo_total_eur
costo_por_paquete_eur
```

The summary is intended to provide the main comparison layer across M1–M5.

For example:

``` text
                   M1   M2   M3   M4   M5
                    │    │    │    │    │
Distance ───────────┼────┼────┼────┼────┤
Trips ──────────────┼────┼────┼────┼────┤
CO₂ ────────────────┼────┼────┼────┼────┤
NOx ────────────────┼────┼────┼────┼────┤
Total cost ─────────┼────┼────┼────┼────┤
Cost/package ───────┼────┼────┼────┼────┤
```

The exact interpretation of an indicator depends on the corresponding logistics model.

For this reason, `summary.csv` should be used together with the methodological definitions presented under **Logistics Models**, rather than interpreting similarly named indicators as necessarily representing identical physical operations across M1–M5.

------------------------------------------------------------------------

### Route-Level Results

Detailed vehicle routes are stored under:

``` text
routes/
```

with model-specific files such as:

``` text
m1_routes.csv
m2_routes.csv
m3_routes.csv
m4_routes.csv
m5_routes.csv
```

These files provide a route-level representation of the operations that produced the aggregated values reported in `summary.csv`.

The relationship is:

``` text
Individual routes
       │
       ▼
Model-level aggregation
       │
       ▼
summary.csv
```

Route-level outputs are useful when the aggregate result alone is insufficient to understand why two logistics models behave differently.

For example, two models may produce similar total distance but substantially different:

- numbers of routes;
- route lengths;
- vehicle utilisation;
- customer distributions;
- service patterns; or
- transport-mode compositions.

The route files therefore support detailed diagnostics and spatial or operational analyses beyond the high-level model comparison.

When route geometry output is enabled, route-level information can additionally include the geometry returned by OSRM, allowing the generated routes to be reconstructed or visualised geographically.

------------------------------------------------------------------------

### Route Stops

When enabled, individual stops are stored in:

``` text
routes/route_stops.csv
```

This file represents the internal sequence of stops associated with the generated routes.

Its granularity differs from the model route files:

``` text
m*_routes.csv
      │
      └── one record describes a route

route_stops.csv
      │
      └── records describe individual stops
          belonging to those routes
```

The stop information can include:

``` text
route identifier
stop position
customer / stop identifier
coordinates
assigned load
arrival time
service start time
service end time
```

Temporal fields such as:

``` text
arrival_datetime
service_start_datetime
service_end_datetime
```

make it possible to reconstruct the progression of a route over the simulated operating period.

Conceptually:

``` text
Route
  │
  ├── Stop 1
  │    ├── arrival
  │    ├── service start
  │    └── service end
  │
  ├── Stop 2
  │    ├── arrival
  │    ├── service start
  │    └── service end
  │
  └── ...
```

This output is particularly useful for temporal analysis, route visualisation, and detailed verification of the simulated operations.

------------------------------------------------------------------------

### Routing Plan Metrics

Routing-algorithm diagnostics are stored separately from the logistics-model results under:

``` text
routing/routing_plan_metrics.csv
```

This distinction is important.

`summary.csv` answers:

> **What operational, environmental, and economic outcome did the logistics model produce?**

whereas `routing_plan_metrics.csv` helps answer:

> **How did the routing algorithm construct or improve the routes used by that model?**

The routing metrics can include fields such as:

``` text
routing_runtime_seconds

initial_distance_km

cws_initial_distance_km
cws_initial_route_count

ils_final_distance_km
ils_final_route_count

ils_improvement_km
ils_improvement_percent

ils_runtime_seconds
ils_iterations_completed
ils_iterations_without_improvement
```

For ILS experiments, this allows the optimisation process to be evaluated independently from the final logistics-model indicators.

For example:

``` text
CWS initial solution
        │
        ├── distance
        └── route count
        │
        ▼
       ILS
        │
        ├── runtime
        ├── iterations
        └── improvement
        │
        ▼
Final routing solution
```

The resulting metrics can be used to evaluate whether the additional computational effort introduced by ILS produces meaningful routing improvements.

This distinction is particularly useful when comparing CWS and ILS experiments because optimisation quality and logistics-model performance are related but are not the same analytical question.

------------------------------------------------------------------------

### Facility Results

Facility-related outputs are stored under:

``` text
facilities/
```

with:

``` text
facility_summary.csv
```

providing information about the facilities involved in the simulated logistics models.

These outputs are primarily relevant to models that depend on intermediate logistics infrastructure, such as PUDO points or microhubs.

Conceptually:

``` text
Demand
   │
   ▼
Facility assignment
   │
   ▼
Selected / used facilities
   │
   ▼
facility_summary.csv
```

The facility results make it possible to inspect which infrastructure elements were involved in the simulation and how demand was associated with them.

They complement the model-level indicators in `summary.csv`.

For example, an aggregate model result may indicate a change in route distance or total cost, while the facility output can help identify the infrastructure configuration associated with that result.

The methodology governing facility selection and assignment is described under **Logistics Models** and the corresponding preprocessing sections.

------------------------------------------------------------------------

### Experiment Metadata

Each experiment can store execution metadata in:

``` text
metadata.json
```

This file records information about the experiment execution and its provenance.

The generated metadata includes information such as:

``` text
git_commit
git_branch
dirty_worktree

config_hash

demand_source_file
demand_file_hash

python_version
platform

runtime_seconds
status
```

The stored hashes make possible to distinguish files that happen to share the same filename but do not contain the same content.

Together with the experiment configuration, this provides a record of the computational conditions associated with a particular result.

------------------------------------------------------------------------

#### Resolved Configuration

When configuration saving is enabled, GLIMS stores:

``` text
config.json
```

inside the experiment output directory.

This file represents the configuration associated with the executed experiment.

It is particularly important when command-line overrides are used.

Conceptually:

``` text
Original JSON
      │
      +
CLI overrides
      │
      ▼
Resolved experiment configuration
      │
      ▼
config.json
```

Therefore, the output configuration should be preserved together with the experiment results.

A result directory containing only the numerical outputs but not its associated configuration and metadata provides substantially less information for later reproduction or auditing.

------------------------------------------------------------------------

### Neighborhood Execution Status

For multi-zone experiments, GLIMS records zone-level execution information in:

``` text
neighborhood_status.csv
```

This file provides a compact view of whether each requested zone completed successfully.

This distinction matters because a multi-zone experiment may encounter a problem in one zone while other zones still produce valid outputs.

Conceptually:

``` text
Experiment
   │
   ├── Zone A ─ completed
   ├── Zone B ─ completed
   ├── Zone C ─ failed
   └── Zone D ─ completed
```

The presence of an experiment-level output directory should therefore not automatically be interpreted as evidence that every requested zone completed successfully.

`neighborhood_status.csv` should be inspected before aggregating or comparing multi-zone results.

------------------------------------------------------------------------

### Routing Integrity and Audit Outputs

GLIMS separates model results from routing-validation information.

The audit directory can contain:

``` text
audit/
├── routing_integrity_summary.csv
├── route_customer_summary.csv
├── unroutable_customers.csv
└── performance_profile.csv
```

These files should be used to verify that the routing solution underlying the model indicators is internally consistent.

------------------------------------------------------------------------

#### Routing Integrity Summary

The principal integrity check is stored in:

``` text
routing_integrity_summary.csv
```

The validation logic compares the demand expected to participate in routing with the demand actually represented in the resulting routes.

At customer level:

``` text
Input customers
       │
       ├── minus excluded / unroutable customers
       │
       ▼
Expected routable customers
       │
       ▼
Compare with customers assigned to routes
```

Conceptually:

$$
N_{\text{expected}} = N_{\text{input}} - N_{\text{excluded}}
$$

and the resulting value is compared against the unique customers assigned to the routing solution.

The audit also checks for inconsistencies such as:

``` text
unassigned customers
duplicated customers
unexpected customer IDs
```

A similar reconciliation is performed for parcel counts.

This is important because an apparently favourable result in `summary.csv` may be misleading if part of the expected demand was accidentally omitted or duplicated during routing.

------------------------------------------------------------------------

#### Route-Customer Summary

The file:

``` text
route_customer_summary.csv
```

provides additional information linking routed customers to the generated route structure.

It can be used when the aggregate integrity result indicates a discrepancy or when a more detailed inspection of customer assignment is required.

The distinction is:

``` text
routing_integrity_summary.csv
        │
        └── Is the routing solution consistent?

route_customer_summary.csv
        │
        └── How are customers represented
            within the routing solution?
```

This makes the audit outputs useful both as automated validation artifacts and as diagnostic information when an experiment behaves unexpectedly.

------------------------------------------------------------------------

#### Unroutable Customers

Customers that cannot be incorporated into a valid routing problem can be recorded in:

``` text
unroutable_customers.csv
```

An unroutable customer should not automatically be treated as a software error.

Depending on the routing instance, a customer may be impossible to serve under the configured constraints or network conditions.

Examples can include situations where:

``` text
customer demand > vehicle capacity
```

or where the network and active route-duration constraints make even an individual:

``` text
Depot → Customer → Depot
```

trip infeasible.

The routing-integrity process distinguishes these explicitly excluded customers from customers that were unexpectedly lost during routing.

This distinction is essential:

``` text
Explicitly unroutable
        ≠
Unexpectedly unassigned
```

The former may represent a valid consequence of the experiment assumptions; the latter may indicate a routing or data-integrity problem that requires investigation.

------------------------------------------------------------------------

### Performance Profiling

When performance profiling is enabled, execution information can be stored in:

``` text
audit/performance_profile.csv
```

This output is intended for computational diagnostics rather than logistics-model comparison.

It can be used to identify expensive stages of an experiment and investigate differences in runtime across:

- cities;
- demand sizes;
- logistics models;
- routing algorithms; or
- optimisation settings.

Performance profiling should therefore be interpreted separately from operational indicators such as distance, emissions, or cost.

A faster algorithm is not necessarily associated with a better logistics solution, and a better routing solution may require additional computational effort.

------------------------------------------------------------------------

### Successful, Partial, and Failed Experiments

Experiment execution and result validity should be treated as separate concepts.

At experiment level, execution metadata may distinguish states such as:

``` text
completed
partial
failed
```

A conceptual interpretation is:

``` text
completed
    │
    └── requested execution completed

partial
    │
    └── some requested components/zones completed,
        while others failed

failed
    │
    └── experiment could not produce the
        required execution
```

A `partial` experiment should not automatically be aggregated or compared with a fully completed experiment without first determining which components are missing.

Likewise, the presence of `summary.csv` does not by itself prove that the corresponding routing solution passed all integrity checks.

------------------------------------------------------------------------

### Validating an Experiment Before Analysis

The recommended validation sequence is:

``` text
Experiment finished
        │
        ▼
1. Check metadata.json
        │
        ▼
Experiment status acceptable?
        │
        ▼
2. Check neighborhood_status.csv
        │
        ▼
All required zones completed?
        │
        ▼
3. Check routing_integrity_summary.csv
        │
        ▼
Expected demand reconciled?
        │
        ▼
4. Inspect unroutable_customers.csv
        │
        ▼
Exclusions understood?
        │
        ▼
5. Inspect routing diagnostics if required
        │
        ▼
6. Analyse summary.csv
```

The main principle is:

> **Result files should be validated before they are interpreted.**

In particular, a low distance, low cost, or low emissions value should not be interpreted as an improvement until it has been confirmed that the expected demand was actually served.

------------------------------------------------------------------------

### Choosing the Appropriate Output Level

Different research questions require different output files.

| Research question                        | Primary output                        |
|:-----------------------------------------|:--------------------------------------|
| How do M1–M5 compare overall?            | `summary.csv`                         |
| How many routes were generated?          | `summary.csv` / model route files     |
| What does an individual route look like? | `routes/m*_routes.csv`                |
| Which customers belong to a route?       | `routes/route_stops.csv`              |
| When are customers reached and served?   | `routes/route_stops.csv`              |
| Which facilities were used?              | `facilities/facility_summary.csv`     |
| How much did ILS improve CWS?            | `routing/routing_plan_metrics.csv`    |
| How long did routing take?               | `routing/routing_plan_metrics.csv`    |
| Did all zones complete?                  | `neighborhood_status.csv`             |
| Was all expected demand routed?          | `audit/routing_integrity_summary.csv` |
| Which customers could not be routed?     | `audit/unroutable_customers.csv`      |
| Which execution produced these results?  | `metadata.json` + `config.json`       |
| Where was computational time spent?      | `audit/performance_profile.csv`       |

This layered structure allows GLIMS results to be analysed without requiring every experiment to be interpreted from the most detailed route-level data.

------------------------------------------------------------------------

### Recommended Analysis Workflow

For comparative experiments, the recommended workflow is:

``` text
                 Experiment outputs
                        │
                        ▼
              Validate execution
                        │
                        ▼
              Validate routing
                        │
                        ▼
              Confirm demand balance
                        │
                        ▼
                 summary.csv
                        │
             ┌──────────┴──────────┐
             │                     │
             ▼                     ▼
      Model comparison       Unexpected result?
                                   │
                                   ▼
                          routing_plan_metrics
                                   │
                                   ▼
                              route details
                                   │
                                   ▼
                          facility / audit data
```

The high-level summary should therefore be the starting point for model comparison, while the detailed outputs provide progressively deeper levels of explanation.

For example, if an experiment shows:

``` text
M4 → lower total distance than M3
```

the analysis can move from:

``` text
summary.csv
```

to:

``` text
route counts
        │
        ▼
individual routes
        │
        ▼
facility assignments
        │
        ▼
routing diagnostics
```

to determine which operational changes produced the difference.

Similarly, when comparing CWS and ILS, the model-level outcome should be considered together with:

``` text
ILS improvement
        +
ILS runtime
        +
final route count
```

rather than evaluating the optimisation algorithm exclusively from its final model cost.

------------------------------------------------------------------------

### Reproducibility of Results

A GLIMS result should ideally be preserved together with:

``` text
Results
   │
   ├── summary.csv
   ├── detailed outputs
   │
   ├── config.json
   ├── metadata.json
   │
   ├── routing validation
   └── execution status
```

This separation between results, diagnostics, and provenance is intentional.

It allows `summary.csv` to remain convenient for large-scale comparative analysis while preserving the information required to investigate, validate, and reproduce the experiment from which each summary row originated.

------------------------------------------------------------------------

## Limitations

The simulator is intended for the evaluation of urban last-mile logistics scenarios under a predefined set of modelling assumptions. In particular:

- Customer demand is synthetically generated during preprocessing, while logistics facilities correspond to real-world locations.
- Customers are assigned to the nearest eligible facility without considering facility capacity constraints. Capacity limits are enforced during route generation.
- Routes are generated using a Clarke-Wright Savings heuristic and therefore do not guarantee globally optimal solutions.
- Travel distances and times depend on locally deployed OSRM services and the underlying OpenStreetMap road network.
- The repository currently provides datasets and routing configurations for Madrid, Barcelona, and Valencia, although additional study areas can be incorporated following the same preprocessing workflow.

These assumptions provide a consistent framework for comparing alternative last-mile delivery strategies across different simulation scenarios.

## Acknowledgements

This work has been developed as part of the GLIMS project in collaboration with the participating research institutions and project partners.

## Citation

If you use this simulator in academic research, please cite the corresponding publication or acknowledge the GLIMS project in your work.

Citation details will be updated once the associated publication becomes available.
