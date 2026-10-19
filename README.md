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
- optional OSRM-snap-based last-meter access adjustment with auditable distance/time outputs;
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

These files centralise the main experimental choices, including the study area, demand instance, routing method, random seeds, simulation constraints, facility-selection behaviour, output options, and other experiment-specific settings. A single configuration can also define batch experiments over multiple demand scenarios and instance sizes, with seed replication applied to each expanded combination.

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

GLIMS also supports an optional last-meter access adjustment derived from OSRM snapping. It can add customer-specific off-network access time to route feasibility while preserving OSRM network distance and access distance as separate auditable components. The default model filter is M1/M2; configuration and output details are documented under [Experiment Configuration](docs/experiment-configuration.md), [Routing and OSRM](docs/routing-and-osrm.md), and [Outputs and Results](docs/outputs-and-results.md).

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

------------------------------------------------------------------------

## Documentation

The main README provides the project overview, architecture, logistics models, installation essentials, and a short execution guide. Detailed technical documentation is kept under [`docs/`](docs/).

| Topic | Detailed documentation |
|---|---|
| City data, boundaries, facilities, Valencia address preparation, and preprocessing utilities | [Data and Preprocessing](docs/data-and-preprocessing.md) |
| Demand-generation pipeline, scenarios, sampling, parcel assignment, and reproducibility | [Demand Generation](docs/demand-generation.md) |
| OSRM services, routing profiles, matrices, cache, failures, and route evaluation | [Routing and OSRM](docs/routing-and-osrm.md) |
| Experiment JSON structure, routing/facility/output settings, seeds, and CLI overrides | [Experiment Configuration](docs/experiment-configuration.md) |
| CWS, BR-CWS, ILS, local search, feasibility, acceptance, and stopping criteria | [Routing Algorithms](docs/routing-algorithms.md) |
| Experiment execution, CLI usage, demand selection, Windows/Linux commands, and sequential runs | [Running GLIMS](docs/running-glims.md) |
| Output directory, result files, audit artifacts, profiling, validation, and analysis workflow | [Outputs and Results](docs/outputs-and-results.md) |

The detailed documents preserve the methodological and implementation-level explanations previously contained in this README; they are separated only to make the repository landing page easier to navigate.

------------------------------------------------------------------------

## Quick Start

After installing the Python dependencies, preparing the required city data and demand instance, and starting the required OSRM services, inspect the simulator interface with:

```bash
python -m code.simulation.osrm_simulator --help
```

The recommended way to run a reproducible experiment is through an experiment configuration file:

```bash
python -m code.simulation.osrm_simulator \\
  --config configs/experiments/<experiment>.json
```

For example:

```bash
python -m code.simulation.osrm_simulator \\
  --config configs/experiments/madrid_embajadores_ils_baseline.json
```

On Windows PowerShell:

```powershell
python -m code.simulation.osrm_simulator --config .\configs\experiments\madrid_embajadores_ils_baseline.json
```

Before running a complete M1–M5 comparison, the generated demand instance must be available and the required `driving`, `cycling`, and `walking` OSRM services should normally be running.

For CLI overrides, demand-instance selection, CWS/ILS selection, sequential execution, and failure handling, see [Running GLIMS](docs/running-glims.md).

------------------------------------------------------------------------

## Outputs at a Glance

Each experiment creates a structured output directory containing aggregated model results, route-level outputs, routing diagnostics, facility information, metadata, and audit artifacts. The principal structure is:

```text
<experiment_id>/
├── config.json
├── metadata.json
├── neighborhood_status.csv
├── summary.csv
├── routes/
├── routing/
├── facilities/
├── audit/
└── neighborhoods/
```

`summary.csv` provides the main aggregated comparison, while the remaining directories preserve the detail required for route analysis, facility analysis, routing diagnostics, reproducibility, and experiment validation.

For the complete file-by-file description and recommended validation/analysis workflow, see [Outputs and Results](docs/outputs-and-results.md).


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
