# open5gs-mcp — Feature Roadmap

Complete inventory of Open5GS NF APIs discovered by source analysis, and recommended tools to build.

---

## Prometheus HTTP Endpoints (`:9090`)

| Endpoint | NF | Contents |
|---|---|---|
| `/metrics` | All NFs | Standard Prometheus scrape |
| `/ue-info` | AMF | supi, guti, cm_state, location (TAI/CGI), gNB, PDU sessions, slices, security, AMBR |
| `/gnb-info` | AMF | gnb_id, PLMN, SCTP peer/streams, TA list, S-NSSAI slices, connected UE count |
| `/pdu-info` | SMF | supi, DNN, S-NSSAI, UE IP, QoS flows (5QI/QFI), N3 GTP-U TEIDs (gNB+UPF) |

---

## AMF OAM API (`http://127.0.0.5:7777/namf-oam/v1`) — h2c

| Method | Path | Action |
|---|---|---|
| `GET` | `/plmns` | List all configured PLMNs + S-NSSAIs + connected gNB/UE counts |
| `POST` | `/plmns` | Add a PLMN at runtime |
| `GET` | `/plmns/{plmn_id}` | Single PLMN detail |
| `DELETE` | `/plmns/{plmn_id}` | Remove PLMN — triggers UE release + gNB disconnect |
| `GET` | `/status` | AMF status |

---

## AMF SBI (`namf-comm/v1`) — h2c

| Method | Path | Action |
|---|---|---|
| `POST` | `/ue-contexts/{supi}/n1-n2-messages` | Send N1/N2 message to UE |
| `GET` | `/ue-contexts/{ueContextId}` | Fetch UE context by context ID |
| `PUT` | `/ue-contexts/{ueContextId}` | Register/update UE context |

---

## NRF SBI (`nnrf-nfm/v1`, `nnrf-disc/v1`) — `http://127.0.0.10:7777`

| Method | Path | Action |
|---|---|---|
| `GET` | `/nf-instances` | List all registered NF instances |
| `GET` | `/nf-instances/{nfInstanceId}` | Single NF profile |
| `PUT` | `/nf-instances/{nfInstanceId}` | Register/update NF |
| `PATCH` | `/nf-instances/{nfInstanceId}` | Heartbeat |
| `DELETE` | `/nf-instances/{nfInstanceId}` | Deregister NF |
| `GET` | `/subscriptions` | List NF status subscriptions |
| `POST` | `/subscriptions` | Subscribe to NF status events |
| `GET` | `/nf-instances` (disc) | NF discovery with filters (nf-type, service-names, PLMN) |

---

## UDM SBI

| Service | Method | Path | Action |
|---|---|---|---|
| `nudm-sdm/v1` | `GET` | `/{supi}/am-data` | AM subscription data |
| `nudm-sdm/v1` | `GET` | `/{supi}/smf-select-data` | SMF selection data |
| `nudm-sdm/v1` | `GET` | `/{supi}/ue-context-in-smf-data` | UE context in SMF |
| `nudm-uecm/v1` | `GET` | `/{supi}/registrations` | AMF + SMF registration records |
| `nudm-uecm/v1` | `PUT` | `/{supi}/registrations/amf-3gpp-access` | AMF registers UE |
| `nudm-ueau/v1` | `POST` | `/{supi}/security-information/generate-auth-data` | Generate auth vectors |

---

## UDR SBI (`nudr-dr/v1`)

| Method | Path | Action |
|---|---|---|
| `GET` | `/subscription-data/{supi}/authentication-data` | Auth credentials (K, OPc, SQN) |
| `GET` | `/subscription-data/{supi}/context-data/amf-3gpp-access` | AMF registration context |
| `GET/PUT/PATCH` | `/subscription-data/{supi}/provisioned-data/am-data` | AM subscription profile |
| `GET/PUT/PATCH` | `/subscription-data/{supi}/provisioned-data/smf-selection-subscription-data` | SMF selection data |
| `GET/PUT` | `/policy-data/ues/{supi}/am-data` | AM policy data |
| `GET/PUT` | `/policy-data/ues/{supi}/sm-data` | SM policy data |

---

## SMF SBI (`nsmf-pdusession/v1`)

| Method | Path | Action |
|---|---|---|
| `POST` | `/sm-contexts` | Create PDU session SM context |
| `POST` | `/sm-contexts/{smContextRef}/modify` | Modify SM context |
| `POST` | `/sm-contexts/{smContextRef}/release` | Release PDU session |
| `GET` | `/sm-contexts/{smContextRef}` | Retrieve SM context |

---

## PCF SBI

| Service | Method | Path | Action |
|---|---|---|---|
| `npcf-am-policy-control/v1` | `POST` | `/policies` | Create AM policy |
| `npcf-smpolicycontrol/v1` | `POST` | `/sm-policies` | Create SM policy |
| `npcf-smpolicycontrol/v1` | `POST` | `/sm-policies/{smPolicyId}/update` | Update SM policy |
| `npcf-smpolicycontrol/v1` | `POST` | `/sm-policies/{smPolicyId}/delete` | Delete SM policy |

---

## BSF SBI (`nbsf-management/v1`)

| Method | Path | Action |
|---|---|---|
| `POST` | `/pcfBindings` | Register PCF binding for a UE IP |
| `GET` | `/pcfBindings` | Discover PCF by UE IP address |
| `DELETE` | `/pcfBindings/{bindingId}` | Deregister binding |

---

## AUSF SBI (`nausf-auth/v1`)

| Method | Path | Action |
|---|---|---|
| `POST` | `/ue-authentications` | Initiate authentication |
| `PUT` | `/ue-authentications/{authCtxId}/5g-aka-confirmation` | Confirm 5G-AKA |
| `PUT` | `/ue-authentications/{authCtxId}/eap-session` | EAP-AKA' session update |

---

## NSSF SBI (`nnssf-nsselection/v1`)

| Method | Path | Action |
|---|---|---|
| `GET` | `/network-slice-information` | Select allowed/target NSSAIs for a UE |

---

## Prometheus Metrics by NF

| NF | Key metrics |
|---|---|
| AMF | `fivegs_amffunction_rm_registeredsubnbr`, `ran_ue` (gauge), `amf_sess` (gauge), `gnb` (gauge) |
| SMF | `ues_active`, `bearers_active`, `gtp2_sessions_active`, `pfcp_sessions_active`, `pfcp_peers_active`, `sm_sessionnbr` (per DNN/slice), `sm_qosflownbr` |
| UPF | `upf_sessionnbr`, `pfcp_peers_active`, `upf_qosflows` (per DNN/slice) |

---

## WebUI REST API (`http://localhost:9999`)

| Method | Path | Action |
|---|---|---|
| `GET/POST` | `/api/db/Subscriber` | List/create subscribers |
| `GET/PUT/DELETE` | `/api/db/Subscriber/{id}` | Read/update/delete subscriber |
| `GET/POST` | `/api/db/Profile` | List/create profiles |
| `GET/PUT/DELETE` | `/api/db/Profile/{id}` | Read/update/delete profile |

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
| **10** | `ue_context_release` | `/namf-oam` or NGAP via AMF | Force-release a UE context by SUPI — closes the loop with `ue_location_query` |

**Highest value picks in order:** `query_nf_metrics` → `gnb_detail_query` → `pdu_session_detail` → `nrf_registry_query` → `plmn_manage`. The first three together give a complete live picture of the data plane from RAN → core → UPF in a single agent call chain.
