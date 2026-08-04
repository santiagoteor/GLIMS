# GLIMS

**Greening City Logistics: Innovative Sustainable Last-Mile Solutions**

## Overview

GLIMS is a research project focused on evaluating alternative strategies for sustainable urban last-mile logistics. This repository contains the simulation framework developed within the project to compare different delivery structures under a common and reproducible simulation environment.

The simulator represents last-mile distribution using customer-level demand instances, existing logistics facilities (logistics centres, microhubs, and pickup and drop-off points), and road-network routing through the Open Source Routing Machine (OSRM). Customer demand is generated through configurable demand scenarios, while all evaluated delivery models operate on the same demand instance to ensure a fair comparison.

The framework is designed to support the analysis of operational, economic, and environmental performance across different urban logistics strategies. It incorporates capacity-constrained routing, route-duration constraints, multiple transportation modes, and existing logistics infrastructure to simulate realistic delivery operations.

This repository includes the complete simulation workflow, from demand generation and facility assignment to route construction and result generation. The simulator currently supports Barcelona, Madrid, and Valencia, and has been developed to facilitate reproducible experiments and comparative analyses within the GLIMS project.

## Repository Structure

The project is organised into modules that separate data preparation, simulation, analysis, and supporting resources. The main directories are described below.

```text
GLIMS/
├── code/
│   ├── analysis/
│   ├── common/
│   ├── preprocessing/
│   └── simulation/
├── data/
├── docs/
├── raw_data/
├── results/
├── requirements.txt
└── README.md
```

| Directory | Description |
|-----------|-------------|
| `code/analysis/` | Scripts used to analyse and organise datasets for subsequent use within the simulation framework. |
| `code/common/` | Shared constants, project paths, utility functions, and reusable resources. |
| `code/preprocessing/` | Scripts for preparing simulation inputs, including demand generation and dataset preprocessing. |
| `code/simulation/` | Main simulation framework, including the OSRM-based simulator and the implementation of the evaluated logistics models. |
| `data/` | Processed datasets required by the simulator, including logistics facilities, input data, and model parameters. |
| `raw_data/` | Original project datasets before preprocessing. |
| `results/` | Output files generated during preprocessing and simulation, including demand instances, location review files, aggregated results, and route-level simulation details. |
| `docs/` | Supplementary project documentation. |

The main entry point of the simulation framework is:

```text
code/simulation/osrm_simulator.py
```

This script implements the current OSRM-based simulation workflow and is responsible for executing the complete logistics simulation process.

## Simulation Workflow

The simulation framework follows a consistent workflow for evaluating all logistics models under identical demand conditions. A previously generated customer demand instance is loaded, neighbourhoods are processed independently, and each delivery model is evaluated using the same customer requests to ensure a fair comparison.

```text
Simulation configuration
        │
        ▼
Load city data, facilities, model parameters,
and an existing demand instance
        │
        ▼
Process each neighbourhood independently
        │
        ▼
Filter customers within the neighbourhood
        │
        ▼
Determine the operational point
and assign customers to facilities
(if required by the model)
        │
        ▼
Retrieve OSRM distance and duration matrices
        │
        ▼
Generate capacity-constrained routes
using the Clarke–Wright Savings algorithm
        │
        ▼
Evaluate logistics models (M1–M5)
        │
        ▼
Aggregate neighbourhood results
and export simulation outputs
```

The main entry point of the simulation framework is:

```text
code/simulation/osrm_simulator.py
```

The simulation consists of the following stages:

1. **Load simulation inputs.**  
   The simulator loads the selected city's logistics centres, neighbourhood boundaries, model parameters, classified logistics facilities, and a previously generated customer demand instance.

2. **Process each neighbourhood independently.**  
   Customer records are filtered according to the neighbourhood boundaries, allowing every neighbourhood to be simulated separately while preserving the same city-wide demand instance.

3. **Determine the operational point.**  
   A demand-weighted centroid is calculated for the neighbourhood and used as the representative operational point for the corresponding delivery model when required.

4. **Assign customers to facilities.**  
   Models that rely on microhubs or PUDOs assign every customer independently to the nearest eligible facility. Facility capacities are not considered during this assignment step; capacity constraints are enforced later during route construction.

5. **Generate routing matrices.**  
   Distance and travel-time matrices are obtained from OSRM using the appropriate transport mode (driving, cycling, or walking). These matrices provide realistic road-network distances and travel times for the routing algorithms.

6. **Construct delivery routes.**  
   Vehicle and courier routes are generated using a capacity- and duration-constrained implementation of the Clarke–Wright Savings algorithm. Route duration includes travel time, stop service time, and route preparation time where applicable.

7. **Evaluate delivery models.**  
   Each logistics model is simulated using the same customer demand, allowing direct comparison of travelled distance, routing performance, emissions, operational costs, and facility utilisation.

8. **Export results.**  
   The simulator generates summary indicators together with detailed route-level outputs, which are stored in the `results/` directory for subsequent analysis.

   ## Delivery Models

The simulator evaluates five alternative last-mile logistics configurations under identical demand conditions. Each model represents a different combination of logistics facilities and transport modes, enabling a direct comparison of operational performance, travelled distance, emissions, and operating costs.

```text
                           Delivery Models

                     ┌─────────────────────┐
                     │ Logistics Centre    │
                     └─────────┬───────────┘
                               │
              ┌────────────────┴────────────────┐
              │                                 │
              ▼                                 ▼
       Direct delivery                 Intermediate facility
         (M1–M2)                          (M3–M5)

                                        ┌──────────────┐
                                        │ Microhub     │──► Cargo bike (M3)
                                        └──────────────┘

                                        ┌──────────────┐
                                        │ PUDO         │──► Walking courier (M4)
                                        │              │──► Customer collection (M5)
                                        └──────────────┘
```

| Model | Logistics configuration | Last-mile transport | Final delivery |
|-------|--------------------------|---------------------|----------------|
| **M1** | Logistics centre → Customer | Conventional van | Home delivery |
| **M2** | Logistics centre → Customer | Electric van | Home delivery |
| **M3** | Logistics centre → Microhub → Customer | Cargo bike | Home delivery |
| **M4** | Logistics centre → PUDO → Customer | Walking courier | Home delivery |
| **M5** | Logistics centre → PUDO | Customer walking | Customer collection |

### Model Description

#### M1 – Conventional Van Delivery

The baseline scenario represents direct home delivery using conventional combustion vans operating from a logistics centre. All deliveries are performed directly to customers without intermediate facilities.

#### M2 – Electric Van Delivery

This model follows the same operational structure as M1 but replaces conventional vans with electric vehicles. It isolates the environmental and operational effects of vehicle electrification while maintaining an identical logistics network.

#### M3 – Microhub with Cargo Bikes

Parcels are transported from the logistics centre to one or more neighbourhood microhubs. Customers are assigned to their nearest microhub, and the final delivery is performed using cargo bikes. This configuration evaluates the potential benefits of replacing conventional urban deliveries with bicycle-based last-mile operations.

#### M4 – PUDO with Walking Couriers

Parcels are first transported to Pick-Up and Drop-Off (PUDO) facilities. Customers are assigned to their nearest PUDO, from which walking couriers complete the final delivery to the customer's address.

#### M5 – Customer Collection from PUDO

Parcels are delivered from the logistics centre to PUDO facilities. Instead of performing the final delivery, customers travel to their assigned PUDO to collect their parcels. The simulator explicitly accounts for customer walking distance and travel time as part of the system evaluation.

## Routing and Simulation Assumptions

To ensure a fair comparison between logistics configurations, all delivery models are evaluated under a common set of assumptions. These assumptions standardise customer demand, facility assignment, routing, travel times, and cost calculations, ensuring that observed differences arise from the logistics configuration itself rather than from changes in the simulation setup.

### Demand

- Customer demand is generated prior to the simulation and loaded as an input dataset.
- All delivery models are evaluated using the **same demand instance**, ensuring identical customer requests across scenarios.
- Demand is represented at the individual customer level.

### Facility Assignment

- Customers are assigned independently to their nearest eligible logistics facility.
- Microhub and PUDO assignments are based solely on geographical proximity.
- Facility capacities are **not** considered during the assignment stage.
- Capacity constraints are enforced later during route construction.

### Routing

- Vehicle routes are generated using the Clarke–Wright Savings heuristic.
- Route construction respects:
  - Vehicle carrying capacity.
  - Maximum route duration.
- Route duration includes:
  - Travel time.
  - Customer service time.
  - Vehicle preparation or loading time where applicable.

### Road Network

- Travel distances and travel times are obtained through the Open Source Routing Machine (OSRM).
- Different routing profiles are used depending on the transport mode:
  - Driving.
  - Cycling.
  - Walking.
- Routes follow the real road network rather than Euclidean distances.

### Cost Model

The operational cost model includes only direct costs incurred during delivery operations.

Included costs:

- Distance-dependent operating costs.
- Labour costs during travel and customer service.
- Per-parcel service fees associated with intermediate facilities.

Excluded costs:

- Vehicle purchase.
- Depreciation.
- Insurance.
- Warehouse operating costs.
- Fixed infrastructure investment.
- Customer travel costs (except when explicitly reported as a performance indicator).

### Performance Indicators

The simulator produces operational indicators that enable direct comparison between logistics models, including:

- Travel distance by transport mode.
- Route duration.
- Number of routes.
- Operational costs.
- CO₂ emissions.
- Facility utilisation.
- Route-level statistics.

## Input Data

The simulator operates on preprocessed datasets generated before the simulation stage. Rather than creating input data during execution, the simulation loads existing datasets describing the study area, logistics facilities, and customer demand.

This separation between preprocessing and simulation ensures that all delivery models are evaluated using identical input data, improving experiment reproducibility and enabling fair comparisons across logistics configurations.

| Dataset | Purpose |
|----------|---------|
| Logistics centres | Existing consolidation centres used as depot origins for all delivery models. |
| Classified logistics facilities | Real-world locations classified as candidate Microhubs or PUDOs during the preprocessing stage. |
| Administrative boundaries | Define neighbourhoods used to divide and process the simulation independently. |
| Demand instances | Customer-level parcel demand generated prior to the simulation and shared across all delivery models. |
| Model parameters | Operational parameters, vehicle characteristics, costs, and emissions used during the simulation. |
| OSRM road network | Real-road travel distances and travel times for driving, cycling, and walking. |

### Demand Instances

Customer demand is generated during the preprocessing stage and stored as an input dataset. Each demand instance represents individual customer parcel requests and is reused across all delivery models, ensuring that every logistics configuration is evaluated under identical demand conditions.

### Logistics Facilities

The simulator uses existing logistics centres together with real-world locations that have been classified during preprocessing as candidate **Microhubs** or **Pick-Up and Drop-Off (PUDO)** facilities. These facilities constitute the logistics network evaluated by the different delivery models.

### Administrative Boundaries

Neighbourhood boundaries are used to partition the study area into smaller processing units. Each neighbourhood is simulated independently before results are aggregated, improving computational efficiency while maintaining a consistent simulation workflow.

### Road Network

Travel distances and travel times are obtained from the Open Source Routing Machine (OSRM). Depending on the delivery model, the simulator uses driving, cycling, or walking routing profiles, ensuring that all route calculations follow the real road network.

## Installation

### Requirements

Before running the simulator, ensure that the following software is installed:

- Python 3.x
- Git
- Docker Desktop (or Docker Engine)

### Clone the Repository

```bash
git clone https://github.com/santiagoteor/GLIMS
cd GLIMS
```

### Create a Virtual Environment

```bash
python -m venv .venv
```

Activate the environment.

**Windows**

```powershell
.venv\Scripts\Activate.ps1
```

**Linux / macOS**

```bash
source .venv/bin/activate
```

### Install Python Dependencies

```bash
pip install -r requirements.txt
```

### Prepare the OSRM Server

The simulator relies on the Open Source Routing Machine (OSRM) to calculate travel distances and travel times over the road network.

The repository includes a setup script that automatically:

- prepares the OSRM datasets for each study area,
- generates the routing data for driving, cycling, and walking profiles,
- and starts the required OSRM servers using Docker.

Run:

```powershell
.\scripts\setup_osrm_city.ps1
```

Once the setup completes successfully, the routing services are ready for use by the simulator.

## Preparing the Input Data

The simulation operates exclusively on preprocessed datasets. Before running any experiments, the required input data must be prepared using the scripts provided in the `preprocessing/` package.

The preprocessing workflow converts raw datasets into the structured files consumed by the simulator.

```text
raw_data/
      │
      ▼
Data preparation
      │
      ├── Prepare logistics facilities
      ├── Convert external datasets
      ├── Generate demand instances
      └── Prepare auxiliary imports
      │
      ▼
data/
results/
      │
      ▼
Simulation
```

### Available Preprocessing Scripts

| Script | Purpose |
|---------|---------|
| `prepare_data.py` | Processes the original datasets, prepares logistics facilities, neighbourhood definitions, and simulation parameters. |
| `generate_instances_demand.py` | Generates customer-level demand instances from real address and population datasets. |
| `json_to_csv.py` | Converts external CityPaq JSON datasets into CSV format for further processing. |
| `prepare_citypaq_import.py` | Prepares candidate CityPaq locations for integration into the logistics facility dataset. |

### Workflow

A typical preprocessing workflow consists of the following steps:

1. Place the required raw datasets inside the `raw_data/` directory.
2. Execute the preprocessing scripts required for the desired study area.
3. Generate the processed datasets and demand instances.
4. Run the simulator using the generated inputs.

Once preprocessing has been completed, the same datasets can be reused across multiple simulation experiments without repeating the preparation process.

## Running Simulations

After completing the preprocessing stage and starting the OSRM services, the simulator can be executed from the project root:

```bash
python -m code.simulation.osrm_simulator
```

Before running the simulation, ensure that:

- The required input datasets have been generated during preprocessing.
- The corresponding OSRM servers are running.
- The simulation parameters have been configured.

The simulator automatically processes the selected study area, evaluates all delivery models (M1–M5), and stores the generated outputs in the `results/` directory.

## Outputs

Simulation outputs are stored in the `results/` directory.

Results are organised by study area and simulation scenario. Each executed scenario generates its own set of output files, allowing independent analysis and comparison across experiments.

```text
results/
├── madrid/
├── barcelona/
└── valencia/
```

### Route Outputs

For every simulated scenario, the simulator exports one route file for each delivery model:

| File | Description |
|------|-------------|
| `m1_routes.csv` | Conventional van routes. |
| `m2_routes.csv` | Electric van routes. |
| `m3_routes.csv` | Microhub + cargo bike routes. |
| `m4_routes.csv` | PUDO + walking courier routes. |
| `m5_routes.csv` | PUDO + customer collection routes. |

Each route file contains one row per generated route together with operational information, including:

- Study area and neighbourhood.
- Delivery model and delivery leg.
- Route identifier.
- Vehicle type and assigned depot.
- Number of stops.
- Vehicle load and capacity.
- Total route distance.
- Total route duration.
- Loading/preparation time.
- Customer service time.
- Stop sequence.

### Summary Outputs

In addition to the detailed route files, the simulator generates aggregated result files that summarise the performance of each experiment. These outputs facilitate comparisons across study areas, scenarios, and delivery models.

## Configuration

The simulator is configured through a combination of global parameters, input datasets, and scenario-specific files. This approach separates the simulation logic from the experiment configuration, making it easier to reproduce and compare different scenarios.

The main configurable components are:

| Component | Description |
|-----------|-------------|
| Global parameters | Shared simulation settings and constants defined in the `common/` module. |
| Input datasets | Processed demand, logistics facilities, administrative boundaries, and other datasets generated during preprocessing. |
| Operational parameters | Vehicle characteristics, routing constraints, service times, operational costs, and emissions used during the simulation. |
| Study area | Selection of the city and neighbourhoods included in each simulation. |
| OSRM routing services | Driving, cycling, and walking routing profiles used to compute travel distances and travel times. |

Most simulation settings can be modified either by updating the corresponding input datasets or by adjusting the global parameters defined in the `common/` module. This design allows experiments to be reproduced while keeping the simulation code independent from scenario-specific configurations.

## Limitations

The simulator is intended for the evaluation of urban last-mile logistics scenarios under a predefined set of modelling assumptions. In particular:

- Customer demand is synthetically generated during preprocessing, while logistics facilities correspond to real-world locations.
- Customers are assigned to the nearest eligible facility without considering facility capacity constraints. Capacity limits are enforced during route generation.
- Routes are generated using a Clarke–Wright Savings heuristic and therefore do not guarantee globally optimal solutions.
- Travel distances and times depend on locally deployed OSRM services and the underlying OpenStreetMap road network.
- The repository currently provides datasets and routing configurations for Madrid, Barcelona, and Valencia, although additional study areas can be incorporated following the same preprocessing workflow.

These assumptions provide a consistent framework for comparing alternative last-mile delivery strategies across different simulation scenarios.

## Acknowledgements

This work has been developed as part of the GLIMS project in collaboration with the participating research institutions and project partners.

## Citation

If you use this simulator in academic research, please cite the corresponding publication or acknowledge the GLIMS project in your work.

Citation details will be updated once the associated publication becomes available.