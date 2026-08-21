# Routing and OSRM

[← Back to the main README](../README.md)

> This document contains the detailed technical documentation extracted from the main GLIMS README.

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

### Last-Meter Access from OSRM Snapping

OSRM routes coordinates by associating them with accessible waypoints on the
selected network. GLIMS preserves the original customer coordinate and the
corresponding snap distance as separate information. When the optional
`last_meter_access` mechanism is enabled, this displacement can also affect
routing feasibility.

The flow is:

``` text
Original customer coordinate
        │
        │ OSRM snapping
        ▼
Network waypoint
        │
        ├── network distance / duration → OSRM matrices
        │
        └── snap distance
                │
                ▼
        last-meter access time
                │
                ▼
       effective customer stop time
                │
                ▼
             CWS / ILS
```

No additional `/nearest` request is required. The snapping metadata already
returned by OSRM table queries is reused. The route-distance objective remains
based on the OSRM network-distance matrix; the access adjustment enters the
temporal feasibility calculation and is exported separately for auditing.

For M1/M2, the default interpretation is a walking round trip between the
vehicle-accessible waypoint and the original customer coordinate. This is why a
customer may remain network-routable while still having a non-zero last-meter
access requirement.

The corresponding audit and output fields are described under **Outputs and
Results**.

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
