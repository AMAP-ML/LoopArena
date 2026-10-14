Build a Python network-emulation and SDN simulation stack in `/workspace`.
The `netsim` library should grow in layers, with each new capability extending
the earlier ones rather than replacing them:

`Mininet core`
Implement the basic node, host, switch, link, and topology abstractions, plus
the simulation engine for things like ping, iperf-style throughput, and RTT.

`Mininet-WiFi`
Add wireless propagation, stations and access points, mobility, signal-aware
wireless links, and automatic association.

`Containernet`
Add container-backed hosts, resource limits, service-function-chain modeling,
and the resource-fairness calculations required by the tests.

`P4 switch`
Add a programmable data plane with parser/deparser stages, match-action
tables, counters, multicast handling, and pipeline-latency modeling.

`SDN controller`
Finish with reactive and proactive controller logic, flow-rule management,
Dijkstra path computation, timeout handling, and link-failure recovery.

Each layer has its own experiment expectations. Keep the module `run.sh`
scripts producing `KEY: VALUE` lines, and use `/tests/<module_name>/test_outputs.py`
plus the `requirements/` files as the source of truth for behavior.
