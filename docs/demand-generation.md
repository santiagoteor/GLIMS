# Demand Generation

[← Back to the main README](../README.md)

> This document contains the detailed technical documentation extracted from the main GLIMS README.

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
