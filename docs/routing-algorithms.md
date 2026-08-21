# Routing Algorithms

[← Back to the main README](../README.md)

> This document contains the detailed technical documentation extracted from the main GLIMS README.

------------------------------------------------------------------------

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
Base service times
    +
Optional customer-specific last-meter access times
    +
Last customer → depot
```

against the permitted limit.

Therefore:

$$
T_r \leq T_{\max}
$$

must hold for every route.

When `last_meter_access.enabled` is active for the corresponding model, each
customer contributes an additional service/access time derived from its OSRM
snap distance. Consequently, CWS merge feasibility, ILS 2-opt feasibility,
restricted-relocate feasibility, and BR-CWS reconstruction all operate under
the same adjusted temporal constraint. The distance objective itself is not
changed by this access penalty.

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
