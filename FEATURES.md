# open5gs-mcp — Feature Roadmap

Analysis of all Open5GS NF APIs and recommended tools to build.

---

## Open5GS API Inventory

| API | Source | What's available |
|---|---|---|
| `GET :9090/ue-info` | AMF | Full UE context: supi, guti, cm_state, location (TAI/CGI), gNB ID, PDU sessions, slices, security, AMBR |
| `GET :9090/gnb-info` | AMF | Per-gNB: gnb_id, PLMN, SCTP peer, TA list, S-NSSAI slices, connected UE count |
| `GET :9090/pdu-info` | SMF | Per-PDU session: supi, DNN, S-NSSAI, UE IP, QoS flows (5QI/QFI), N3 GTP-U TEIDs (gNB+UPF sides) |
| `GET :9090/metrics` | All NFs | Prometheus: AMF (RAN UEs, gNBs, sessions), SMF (active UEs/bearers/PFCP sessions), UPF (session count, QoS flows, PFCP peers) |
| `GET /namf-oam/v1/plmns` | AMF OAM | Configured PLMNs + S-NSSAIs + connected gNB/UE counts |
| `GET /namf-oam/v1/plmns/{id}` | AMF OAM | Single PLMN detail |
| `POST /namf-oam/v1/plmns` | AMF OAM | Add a PLMN at runtime |
| `DELETE /namf-oam/v1/plmns/{id}` | AMF OAM | Remove a PLMN (triggers UE release + gNB disconnect) |
| `GET /nnrf-nfm/v1/nf-instances` | NRF | All registered NF instances with type, status, addresses, heartbeat |
| `GET /nudm-sdm/v1/{supi}/am-data` | UDM SBI | AM subscription data for a SUPI |
| `GET /nudm-uecm/v1/{supi}/registrations` | UDM SBI | AMF/SMF registration context for a SUPI |
| `GET /nudr-dr/v1/subscription-data/{supi}` | UDR SBI | Raw subscription record |
| WebUI REST (`/api/db/Subscriber`) | WebUI | Full subscriber CRUD with profile |

---

## Top 10 Tools to Build

| # | Tool | API Used | Why it showcases |
|---|---|---|---|
| **1** | `query_nf_metrics` | `:9090/metrics` (all NFs) | Scrape and parse Prometheus from any NF — most universal, every NF has it |
| **2** | `gnb_detail_query` | `:9090/gnb-info` | Rich per-gNB state: SCTP, TA list, slices, UE count — great for RAN troubleshooting |
| **3** | `ue_location_query` | `:9090/ue-info` | UE TAI/CGI, gNB, cm_state, slice — real-time location and registration state |
| **4** | `pdu_session_detail` | `:9090/pdu-info` | Per-session UE IP + GTP-U TEIDs (gNB+UPF) — proves data plane is up |
| **5** | `nrf_registry_query` | `/nnrf-nfm/v1/nf-instances` | See all registered NFs, their IPs, status, heartbeat — service mesh visibility |
| **6** | `plmn_manage` | `/namf-oam/v1/plmns` (GET/POST/DELETE) | Add/remove PLMNs live — only write-capable OAM op, dramatic demo |
| **7** | `subscriber_auth_reset` | MongoDB direct | Update K/OPc/SQN for a SUPI — pairs with `subscriber_crud`, completes auth lifecycle |
| **8** | `patch_nf_config` | NF YAML files | Live config patch for any NF (DNN pool, slices, SBI endpoints) — config-as-code demo |
| **9** | `network_infra_check` | `ip` / `tun` syscalls | Verify ogstun, UPF route, GTP kernel module — pre-flight check before attach |
| **10** | `ue_context_release` | `/namf-oam` or NGAP via AMF | Force-release a UE context by SUPI — operational action, closes the loop with `ue_location_query` |

**Highest value picks in order:** `query_nf_metrics` → `gnb_detail_query` → `pdu_session_detail` → `nrf_registry_query` → `plmn_manage`. The first three together give a complete live picture of the data plane from RAN → core → UPF in a single agent call chain.
