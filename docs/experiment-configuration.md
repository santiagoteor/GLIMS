# Experiment Configuration

[← Back to the main README](../README.md)

> This document contains the detailed technical documentation extracted from the main GLIMS README.

------------------------------------------------------------------------

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
├── last_meter_access
│   ├── enabled
│   ├── walking speed
│   ├── round-trip access
│   └── model filter
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

### Last-Meter Access Configuration

Optional off-network customer access is configured through:

``` json
"last_meter_access": {
  "enabled": true,
  "walking_speed_m_s": 1.2,
  "round_trip": true,
  "models": ["M1", "M2"]
}
```

The feature uses the distance between each original demand coordinate and the
waypoint selected by the corresponding OSRM profile. It does **not** make the
customer unroutable. Instead, the configured access distance is converted into
an additional customer-specific time and incorporated into route-duration
feasibility before CWS/ILS constructs or modifies routes.

| Parameter | Description |
|:--|:--|
| `enabled` | Enables the last-meter access adjustment. The default is `false` so existing experiments remain backward compatible. |
| `walking_speed_m_s` | Walking speed used to convert off-network access distance into time. Default: `1.2` m/s. |
| `round_trip` | When `true`, the one-way OSRM snap distance is multiplied by two to represent access from the network waypoint to the customer and back. |
| `models` | Models to which the adjustment applies. The explicit default is `["M1", "M2"]`. `null` or an empty list means all supported customer-facing models. |

For M1 and M2, the interpretation is:

``` text
Driving network waypoint
        │
        │ walk to customer
        ▼
     Customer
        │
        │ return to vehicle
        ▼
Driving network waypoint
```

For a customer with one-way snap distance $d_i$ and walking speed $v$, the
round-trip access time is:

$$
t_i^{access} = \frac{2d_i}{60v}
$$

when `round_trip` is enabled. The effective stop time is therefore:

$$
t_i^{stop} = t^{base}_{service} + t_i^{access}
$$

where the current base service time is 5 minutes. The access time affects route
feasibility and route/labour duration, while the OSRM network distance remains
separately identifiable in the outputs.

When `models` is `null` or empty, the same mechanism is enabled for all
customer-facing model legs. Facility-supply routes are not penalised because
the adjustment represents access to the final customer, not unloading at an
intermediate facility. For M5, which has no 5-minute home-delivery service
operation, the snap access is added to the customer round-trip distance/time
without introducing a delivery-service stop.

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
