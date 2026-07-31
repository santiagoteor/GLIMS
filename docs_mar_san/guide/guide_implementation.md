## Module 1. Preparation and integreation of data

### Used data

**Urban Infrastructure**

* OpenStreetMap (osrm y osmnx)
* Road Network
* Bicycle Network
* Pedestrian Network

**Logistics Points**

* PUDO Points
* Lockers
* Partner Stores
* Microhubs
* CC 

**Demographic Data**

* Population
* Population Density
* Income (maybe not neccesary if we have in mind density of logistic points as the only variable for income and vulnerability)
* Vulnerability Index (still idk how to do that 23/07/2026)

**Logistics Data**

* Historical Orders (could be simulated I think)
* Delivery Addresses (could be simulated I think)
* Time Windows (maybe we have to take a look to the article in ESADE about time windows) (maybe not necessary, too much complex 29072026)

## Module 2: Prediction of demand with AI

We have to predict where, when and how much about the orders

### Used data

**In my opinion** we need a BBDD with data of this type: 

* Day of the Week
* Time of Day
* Weather
* Marketing Campaigns
* Christmas
* Black Friday
* Population
* Income
* Land Use
* Traffic


### Posible algorithms 

#### Possible because ML methods are similar to AI
* XGBoost
* LightGBM
* Random Forest

#### Other methods more similar to traditional AI or with temporal components
* Prophet
* ARIMA

#### KPIs
* MAE
* RMSE
* MAPE
* R^2


<!-- ## Module 3: Optimum location for instalations

### Objetive: Choose how many centers and where have they to be located

### Used data

We have to use: 

* Predictions of module 2 to know all the demand
* Data of module 1 -->

### Tools

**LRP/VRP Problem**

**Algorithms**

Clarke & Wright
Multi-start (could be better to compare)
Maybe another, I have to research

**Metaheuristics**

ILS
GRASP
Tabu Search (could be better to compare)
Simulated Annealing
Genetic Algorithms

(We have to research too about assigment logistic points and clients)

### KPIs

*total cost*
*coverage*
*average distance*
*time*
*Used capacity*
*travel number*
*emissions*

## Module 4: Route Optimization (PR2)

### Objective

Find the optimal delivery routes for each vehicle once the logistics facilities (consolidation centers, microhubs and PUDOs) have been selected.

### Used data

From previous modules:

* Optimal locations from **Module 3** or real locations in the BBDD
* Predicted demand from **Module 2**
* Customer locations
* Road network
* Traffic conditions (real-time or historical) (I think we can put them in OSRM)
* Vehicle capacities
* Delivery time windows
* Vehicle speeds depending on transport mode (van, cargo bike, walking)
* Service times at each customer

### Possible tools

#### Vehicle Routing Problem (VRP)

Depending on the scenario, different VRP variants can be considered:

* Capacitated VRP (CVRP)
* VRP with Time Windows (VRPTW)
* Multi-Depot VRP (MDVRP)
* Two-Echelon VRP (2E-VRP) for microhub-based distribution

#### Optimization libraries

* Google OR-Tools
* PyVRP
* VROOM (optional)

#### Heuristics / Metaheuristics

* Clarke-Wright Savings
* Iterated Local Search (ILS)

#### Others to do it better or compare

* Adaptive Large Neighborhood Search (ALNS)
* Genetic Algorithms
* Tabu Search (optional)

### Output

The model generates:

* Optimal customer sequence
* Distance travelled
* Travel time
* Vehicle utilization

### KPIs

'(1) km recorreguts en furgoneta, (2) km recorreguts
en bicicleta, (3) km caminats pels reparƟdors, (4) km caminats pels clients, (5) nombre de
viatges en furgoneta, (6) nombre de viatges en bicicleta, (7) nombre de viatges a peu dels
reparƟdors, (8) nombre de viatges a peu dels clients, (9) emissions totals, idealment amb
diversos indicadors; si no és possible, CO₂, i (10) cost total del sistema.'

---

# Module 5: Scenario Comparison

### Objective

Compare the four last-mile logistics configurations under the same demand conditions.

### Scenarios

**Scenario 1**

* Consolidation Center → Customer (Electric Van)

**Scenario 2**

* Consolidation Center → Microhub → Cargo Bike

**Scenario 3**

* Consolidation Center → PUDO → Walking Delivery

**Scenario 4**

* Consolidation Center → PUDO → Customer Pickup

### Used data

* Outputs from Modules 3 and 4
* Predicted demand
* Vehicle characteristics
* Infrastructure constraints
* Operational costs
* Emission factors
* Energy consumption factors

### Tools

No additional optimization algorithms are required.

The complete optimization model is executed four times by modifying:

* Logistics configuration
* Vehicle type
* Last-mile transport mode
* Infrastructure used
* Operational constraints

### Output

A comparative performance table for all scenarios.

### KPIs

'(1) km recorreguts en furgoneta, (2) km recorreguts
en bicicleta, (3) km caminats pels reparƟdors, (4) km caminats pels clients, (5) nombre de
viatges en furgoneta, (6) nombre de viatges en bicicleta, (7) nombre de viatges a peu dels
reparƟdors, (8) nombre de viatges a peu dels clients, (9) emissions totals, idealment amb
diversos indicadors; si no és possible, CO₂, i (10) cost total del sistema.'
---

<!-- # Module 6: Flexible Microhubs (New Scientific Contribution)

### Objective

Evaluate whether dynamically relocating microhubs improves the efficiency of urban logistics compared to fixed microhubs.

### Used data

Everything generated in previous modules plus:

* Daily or weekly demand prediction
* Candidate microhub locations
* Relocation costs
* Installation/removal time
* Availability of candidate locations
* Historical demand variability
* Operational constraints

### Possible tools

#### AI Prediction

* XGBoost
* LSTM
* Temporal Transformer

#### Dynamic Optimization

* Dynamic Facility Location Problem
* Dynamic Location-Routing Problem (DLRP)
* Rolling Horizon Optimization

#### Advanced methods (optional)

* Reinforcement Learning
* Digital Twin simulation

### Workflow

For each planning period:

```text
Week 1

Demand Prediction
        ↓
Network Optimization
        ↓
Microhub A
```

```text
Week 2

Demand Prediction
        ↓
Network Optimization
        ↓
Microhub B
```

```text
Week 3

Demand Prediction
        ↓
Network Optimization
        ↓
Microhub A
```

Finally, compare:

```text
Fixed Microhub

vs

Flexible Microhub
```

### KPIs

* Number of relocations
* Relocation cost
* Operational savings
* Distance reduction (km)
* CO₂ reduction
* Average delivery time
* Service level (%)
* Microhub utilization (%)
* Return on Investment (ROI)
* Payback period

--- -->

# Module 7: Final Evaluation and Decision Support

### Objective

Compare all logistics strategies and identify the most efficient solution under different urban conditions.

### Used data

Outputs from all previous modules.

### Possible tools

#### Data analysis

* Python
* Pandas
* NumPy

#### Visualization

* Power BI
* Tableau
* Plotly

#### Statistical analysis

* ANOVA
* Sensitivity Analysis
* Monte Carlo Simulation (optional)
* Pareto Analysis

### Output

Final comparison table.

| Scenario | Cost | Emissions | Delivery Time | Distance | Service Level |
|----------|------|-----------|---------------|----------|---------------|
| Direct Delivery | | | | | |
| Fixed Microhub | | | | | |
| Flexible Microhub | | | | | |
| PUDO | | | | | |

### KPIs

* Best-performing scenario
* Total operational cost
* Total CO₂ emissions
* Total travelled distance
* Energy consumption
* Average delivery time
* Service level
* Network efficiency
* Cost per delivered package
* Sensitivity to demand changes
* Sustainability score
