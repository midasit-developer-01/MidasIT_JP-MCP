# MIDAS API Reference (refined edition)

> A summary for **Claude to reference in one place** for the MIDAS NX series (CIVIL NX / GEN NX) Open API.
> The full and per-item detailed schemas follow the official manual; this document organizes the **structure, conventions, and the full endpoint catalog**.
> The call site in this template is [`utils_api.ts`](./utils_api.ts), whose prototype is [`../public/py_base.py`](../public/py_base.py).
> **Real request/response payload examples** are kept separately in [`./midas-api-examples.json`](./midas-api-examples.json).

> **🧭 Usage rules (important)**
> 1. If a needed endpoint **is in this document's catalog, use it as-is** — do not re-check external links.
> 2. If you need the **exact field schema of an endpoint**, fetch it directly from the **Zendesk public API** using the `article` number in the catalog below:
>    ```
>    GET https://support.midasuser.com/api/v2/help_center/articles/<article_id>.json
>    Header: User-Agent: Mozilla/5.0 ... Chrome/124.0 Safari/537.36
>    ```
>    The response `article.body` (HTML) contains the **Input URI / Methods / JSON Schema / Examples / Specifications tables**.
>    (The browser HTML page returns 403 due to bot blocking. WebFetch won't work → use PowerShell `Invoke-WebRequest -Headers` / curl.)
> 3. **Reflect the obtained field schema/examples into `midas-api-examples.json`** so you don't have to look them up again.

**Sources**
- [MIDAS API Online Manual (index)](https://support.midasuser.com/hc/en-us/articles/33016922742937-MIDAS-API-Online-Manual)
- [Base URL and API-KEY](https://support.midasuser.com/hc/en-us/articles/30508934444569-Base-URL-and-API-KEY)
- [Introduction - MIDAS Python](https://midas-rnd.github.io/midasapi-python/)
- [Designing with Intent: The Vision Behind POST/TABLE](https://support.midasuser.com/hc/en-us/articles/45171987915929-Designing-with-Intent-The-Vision-Behind-POST-TABLE)

> **Marker legend**: `ᴴˢ` = Hyper-S solver only, `ᴶ` = CIVIL NX JP version only.

---

## 1. Basic structure

### Base URL
```
https://{server}:443/{program}
```
- `{program}`: `civil` (Civil NX) or `gen` (Gen NX)
- `{server}` (by region):
  | Region | Server |
  | --- | --- |
  | Global/KR | `moa-engineers.midasit.com` (this template's default) |
  | Korea | `moa-engineers-kr.midasit.com` |
  | India | `moa-engineers-in.midasit.com` |
  | Europe | `moa-engineers-gb.midasit.com` |
  | USA | `moa-engineers-us.midasit.com` |
  | China | `moa-engineers.midasit.cn` |
- e.g. `https://moa-engineers.midasit.com:443/civil`

### Authentication (MAPI-Key)
- Put the product key in the **header** of every request.
  ```
  MAPI-Key: {ISSUED_KEY}
  Content-Type: application/json
  ```
- The key is issued from **API Settings** in the MIDAS CIVIL/GEN NX app and must match the key configured in the app.
- Precondition: the target MIDAS NX app must be running and a model file must be open (requests act on the "open file").

---

## 2. Common request/response conventions

A request = a combination of **method + command (endpoint) + body (JSON)**; the MIDAS API is a RESTful API.

| Endpoint group | top-level body key | Notes |
| --- | --- | --- |
| **DB** (POST/PUT) | `"Assign"` | `{ "Assign": { "<number key>": { ...properties } } }` |
| **DB** (GET) | (usually no body) | The first key is the endpoint (item) name |
| **DOC** | `"Argument"` | `{ "Argument": <value> }` (POST only; an empty body is `{}`) |
| GET / DELETE | (usually no body) | |

- **The DB "number key" means different things per data type.** e.g. in Section it's the section ID, in Load Combination it's the creation sequence.
  Single-data items like UNIT have only `"1"`.
- DB responses usually wrap the result with the **item name as the top-level key**.
  e.g. `GET /db/NODE` → `{ "NODE": { "1": {...}, "2": {...} } }`
  → this template's `utils_api.dbRead()` strips this `NODE` key and returns it as `{ "1": {...} }`.
- On failure, the response includes an `error`-type message (this template unifies it under the `{ error }` convention).

---

## 3. The 5 endpoint categories

| Group | Path | Method | body | Description |
| --- | --- | --- | --- | --- |
| **DOC** | `/doc/...` | POST | `Argument` | Document/file control (open, save, analyze, import/export) |
| **DB** | `/db/{ITEM}` | POST/GET/PUT/DELETE | `Assign` | Model data stored in the file (corresponds to MCT/MGT). Some (UNIT, STYP, etc.) are GET/PUT only |
| **OPE** | `/ope/...` | (per feature) | — | Operations. GUI control / pre-processing values not stored in the DB (mesh, division, section calc, etc.) |
| **VIEW** | `/view/...` | (per feature) | — | Model view control (selection, capture, display, result graphics) |
| **POST** | `/post/...` | POST | `Argument` | Pre/post-processing table extraction and design checks (`/post/TABLE`, design forces, etc.) |

---

## 4. DOC — Document/file control `(/doc/...)`

**POST only**, body uses the `"Argument"` key. An empty body is `{}`.

| Endpoint | Role | Manual article |
| --- | --- | --- |
| `/doc/NEW` | New project | 35994078198681 |
| `/doc/OPEN` | Open project | 35994112560793 |
| `/doc/CLOSE` | Close project | 35994162529305 |
| `/doc/SAVE` | Save | 35994210207513 |
| `/doc/SAVEAS` | Save as | 35994277012377 |
| `/doc/STAGAS` | Save current construction stage as | 50707525717401 |
| `/doc/IMPORT` | Import from JSON | 35994338816793 |
| `/doc/IMPORTMXT` | Import from mct/mgt | 35994365225113 |
| `/doc/EXPORT` | Export to JSON | 35994422273305 |
| `/doc/EXPORTMXT` | Export to mct/mgt | 35994462805017 |
| `/doc/ANAL` | Run analysis | 35685160815897 |

---

## 5. DB — Model data CRUD `(/db/{ITEM})`

Basics: `GET /db/{ITEM}` (all), `GET /db/{ITEM}/{id}` (single), `POST` (create), `PUT` (update), `DELETE /db/{ITEM}/{id}` (delete).
(Exception: required-for-new-file data like `UNIT`, `STYP` are **GET/PUT only**.)

### 5-1. Project
| Path | Role | article |
| --- | --- | --- |
| `/db/PJCF` | Project information | 35801869341337 |
| `/db/UNIT` | Unit system (FORCE/DIST/HEAT/TEMPER) — GET/PUT only | 35802155483801 |
| `/db/STYP` | Structure Type — GET/PUT only | 35802404495257 |
| `/db/STYP-M1` ᴴˢ | Structure Type (Hyper-S) | 56375311138201 |
| `/db/GRUP` | Structure group | 35802441712921 |
| `/db/BNGR` | Boundary condition group | 35804937452313 |
| `/db/LDGR` | Load group | 35804975346841 |
| `/db/TDGR` | Tendon group | 35805198736793 |

### 5-2. View (DB-stored colors/planes)
| Path | Role | article |
| --- | --- | --- |
| `/db/NPLN` | Named Plane | 35805287066649 |
| `/db/CO_M` | Material color | 35805703171353 |
| `/db/CO_S` | Section color | 35805763514393 |
| `/db/CO_T` | Thickness color | 35805833925785 |
| `/db/CO_F` | Floor-load color | 35805846236441 |

### 5-3. Structure
| Path | Role | article |
| --- | --- | --- |
| `/db/SPAN` | Span information | 35805957502233 |
| `/db/STOR` | Story data | 49513466793113 |

### 5-4. Node / Element
| Path | Role | article |
| --- | --- | --- |
| `/db/NODE` | Node (coordinates X,Y,Z) | 35806845654169 |
| `/db/ELEM` | Element (node composition / element type) | 35806934300825 |
| `/db/SKEW` | Node Local Axis | 35807178748569 |
| `/db/MADO` | Define Domain | 35807228332825 |
| `/db/SBDO` | Define Sub-Domain | 35807304820761 |
| `/db/DOEL` | Domain-Element | 35807341514393 |

### 5-5. Properties
| Path | Role | article |
| --- | --- | --- |
| `/db/MATL` | Material properties | 35807411331993 |
| `/db/MATL-M1` ᴴˢ | Material properties (Hyper-S) | 56396523438873 |
| `/db/IMFM` | Fiber-model inelastic material | 35807475893401 |
| `/db/IMFM-M1` ᴴˢ | Inelastic material auto-generation link (Hyper-S) | 56375076523929 |
| `/db/TDMF` | Time-dependent material - user-defined | 35807665049369 |
| `/db/TDMT` | Time-dependent material - creep/shrinkage | 35808006330009 |
| `/db/TDME` | Time-dependent material - compressive strength | 35808102389401 |
| `/db/EDMP` | Change Property (element-dependent material) | 35808245801881 |
| `/db/TMAT` | Time-dependent material link | 35808280891033 |
| `/db/EPMT` | Plastic Material | 35808376517913 |
| `/db/EPMT-M1` ᴴˢ | Plastic Material (Hyper-S) | 56511025581337 |
| `/db/SECT` | Section properties (Common/DB·User/Value/SRC/Combined/PSC/Composite/Tapered ...) | 35808653964185 et al. |
| `/db/THIK` | Thickness (Value/Stiffened DB·User·Value) | 35942236652697 et al. |
| `/db/TSGR` | Tapered (variable-section) group | 35942955627673 |
| `/db/SECF` | Section Manager - Stiffness | 35943174833177 |
| `/db/RPSC` | Section Manager - Reinforcements | 35943227821465 |
| `/db/STRPSSM` | Section Manager - Stress Points | 35943448721177 |
| `/db/PSSF` | Section Manager - Plate Stiffness Scale Factor | 35943557337753 |
| `/db/VBEM` | Section for Resultant - Virtual Beam | 35943802727065 |
| `/db/VSEC` | Section for Resultant - Virtual Section | 35943859944729 |
| `/db/EWSF` | Effective Width Scale Factor | 35943954272281 |
| `/db/IEHP` ⚠️ | **Inelastic hinge property definition** (the actual M-φ skeleton curve / hysteresis-model data). **Undocumented** — not in the manual/catalog, but GET/PUT verified working on the live REST server. See §10-1 | (none) |
| `/db/IEHC` | Inelastic hinge control data | 35944093809689 |
| `/db/IEHG` | Inelastic hinge property assignment (element↔property-name only) | 35944228031001 |
| `/db/IEHG-BEAM-M1` ᴴˢ | Inelastic hinge - Beam | 57656773423385 |
| `/db/IEHG-TRUSS-M1` ᴴˢ | Inelastic hinge - Truss | 57656796689177 |
| `/db/IEHG-GL-M1` ᴴˢ | Inelastic hinge - General Link | 57656799110937 |
| `/db/IEHG-PSS-M1` ᴴˢ | Inelastic hinge - Point Spring Support | 57656826629657 |
| `/db/FIMP` | Inelastic material properties | 35944335180569 |
| `/db/FIBR` | Section Fiber division | 35944476555801 |
| `/db/GRDP` | Group Damping | 35944577940633 |
| `/db/ESSF` | Element Stiffness Scale Factor | 44613910309401 |

### 5-6. Boundary
| Path | Role | article |
| --- | --- | --- |
| `/db/CONS` | Constraint Support | 35944759597337 |
| `/db/NSPR` | Point Spring | 35945908301081 |
| `/db/GSTP` | General spring type definition | 35946004118169 |
| `/db/GSPR` | General spring support assignment | 35946151002393 |
| `/db/SSPS` | Surface Spring | 35946218805785 |
| `/db/ELNK` | Elastic Link | 35946439146649 |
| `/db/RIGD` | Rigid Link | 35946584247193 |
| `/db/NLLP` | General link properties | 35946764618905 |
| `/db/NLNK` | General Link | 35946942651289 |
| `/db/NLNK-M1` ᴴˢ | General Link (Hyper-S) | 56511465190937 |
| `/db/CGLP` | General link property change | 35947087784217 |
| `/db/FRLS` | Beam End Release | 35947184258585 |
| `/db/OFFS` | Beam End Offset | 35947465569049 |
| `/db/PRLS` | Plate End Release | 35947668757017 |
| `/db/MLFC` | Force-Deformation Function | 35947795463705 |
| `/db/SDVI` | Seismic device - viscous/oil damper | 35947995586713 |
| `/db/SDVE` | Seismic device - viscoelastic damper | 35948062417049 |
| `/db/SDST` | Seismic device - steel damper | 35948150053529 |
| `/db/SDHY` | Seismic device - Hysteretic Isolator (MSS) | 35948292269977 |
| `/db/SDIS` | Seismic device - Isolator (MSS) | 35948330042649 |
| `/db/MCON` | Linear Constraints (multi-point constraint) | 35948507217689 |
| `/db/PZEF` | Panel Zone Effects | 35950231812505 |
| `/db/CLDR` | Constraints Label Direction definition | 35952465579417 |
| `/db/DRLS` | Diaphragm Disconnect | 51740138178969 |

### 5-7. Static Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/STLD` | Static load case | 35952651947801 |
| `/db/BODF` | Self-Weight | 35952708909337 |
| `/db/CNLD` | Nodal Load | 35952812160281 |
| `/db/BMLD` | Beam Load | 35952826746521 |
| `/db/SDSP` | Specified Displacement | 35952933108761 |
| `/db/NMAS` | Nodal Mass | 35952994344985 |
| `/db/LTOM` | Loads to Masses | 35953062761881 |
| `/db/NBOF` | Nodal Body Force | 35953117115545 |
| `/db/PSLT` | Pressure load type definition | 35953165879833 |
| `/db/PRES` | Pressure load assignment | 35953322434457 |
| `/db/PNLD` | Plane load type definition | 35953492119321 |
| `/db/PNLA` | Plane load assignment | 35953557411993 |
| `/db/FBLD` | Floor load type definition | 35953604106137 |
| `/db/FBLA` | Floor load assignment | 35953653792665 |
| `/db/FMLD` | Finishing Material load | 35953690148121 |
| `/db/POSP` | Soil property parameters | 49510865840537 |
| `/db/EPST` | Static Earth Pressure | 49511059178521 |
| `/db/EPSE` | Seismic Earth Pressure | 49511153905177 |
| `/db/POSL` | Seismic load parameters | 49511410691609 |

### 5-8. Temperature Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/ETMP` | Element Temperature | 35954097233561 |
| `/db/GTMP` | Temperature Gradient | 35954163821593 |
| `/db/BTMP` | Beam Section Temperature | 35954186047897 |
| `/db/STMP` | System Temperature | 35954219102233 |
| `/db/NTMP` | Nodal Temperature | 35954302641177 |

### 5-9. Prestress Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/TDNT` | Tendon Property | 35954451663513 |
| `/db/TDNA` | Tendon Profile | 35954555962137 |
| `/db/TDCS` | Composite-section tendon position | 35954649371545 |
| `/db/TDPL` | Tendon Prestress | 35954702397209 |
| `/db/PRST` | Prestress beam load | 35954744402713 |
| `/db/PTNS` | Post-tension / jacking-force load | 35954793469593 |
| `/db/EXLD` | External load case for Pretension | 35954841849753 |

### 5-10. Moving Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/MVCD` | Moving Load Code | 35955076795929 |
| `/db/LLAN` | Traffic Line Lanes | 35955170613273 |
| `/db/LLANch` / `LLANid` / `LLANtr` / `LLANop` | Lanes - China/India/Transverse/MovingLoadOpt | 35955208713241 et al. |
| `/db/SLAN` | Surface Lanes | 35956862556313 |
| `/db/SLANch` / `SLANop` | Surface lanes - China/MovingLoadOpt | 35956917206425 et al. |
| `/db/MVHL` | Vehicles (by code: AASHTO/Eurocode/Korea/China/India ...) | 35957125531545 et al. |
| `/db/MVHLtr` | Vehicles - Transverse | 35958910039321 |
| `/db/MVLD` | Moving Load Cases | 35959068573209 |
| `/db/MVLDch` / `MVLDid` / `MVLDbs` / `MVLDeu` / `MVLDpl` / `MVLDtr` | Moving load cases - by code | 35960417354649 et al. |
| `/db/CRGR` | Concurrent Reaction Group | 35962261902745 |
| `/db/CJFG` | Concurrent Joint Force Group | 35962376351769 |
| `/db/MVHC` | Vehicle Classes | 35962463156761 |
| `/db/SINF` | Plate elements for influence surface | 35962659347481 |
| `/db/MLSP` | Lane Support - Negative Moments | 35962967211545 |
| `/db/MLSR` | Lane Support - Reactions | 35963167875225 |
| `/db/DYLA` | Dynamic Load Allowance | 35963288573849 |
| `/db/IMPF` | Additional Impact Factor | 35963359844889 |
| `/db/DYFG` | Railway Dynamic Factor | 35963474883097 |
| `/db/DYNF` | Railway Dynamic Factor by Element | 35963520535577 |

### 5-11. Dynamic Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/SPFC` | Response spectrum function (User/by code) | 35963686253593 et al. |
| `/db/SPLC` | Response spectrum load case | 35963719599641 |
| `/db/THGC` | Time-history global control | 35963819140505 |
| `/db/THGC-M1` ᴴˢ | Time-history global control (Hyper-S) | 56510942223513 |
| `/db/THOO-M1` ᴴˢ | Time-history output options (Hyper-S) | 56371398681241 |
| `/db/THIS` | Time-history load case | 35963903917593 |
| `/db/THIS-M1` ᴴˢ | Time-history load case (Hyper-S) | 56538335819673 |
| `/db/THFC` | Time-history function | 35964507702937 |
| `/db/THGA` | Ground Acceleration | 35964590740633 |
| `/db/THNL` | Dynamic nodal load | 35964586306841 |
| `/db/THSL` | Time-varying static load | 35964656837785 |
| `/db/THMS` | Multiple Support Excitation | 35964708397081 |

### 5-12. Construction Stage Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/STAG` | Construction stage definition | 35987578396697 |
| `/db/CSCS` | Construction-stage composite section | 35987625234201 |
| `/db/TMLD` | Construction-stage Time Loads | 35987743311385 |
| `/db/STBK` | Nonlinear construction-stage Set-Back load | 35987833076505 |
| `/db/CMCS` | Construction-stage Camber | 35987807611161 |
| `/db/CRPC` | Construction-stage creep coefficient | 35987878971545 |

### 5-13. Heat of Hydration Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/ETFC` | Ambient temperature function | 35988049086489 |
| `/db/CCFC` | Convection coefficient function | 35988168533785 |
| `/db/HECB` | Element convection boundary | 35988210740761 |
| `/db/HSPT` | Prescribed Temperature | 35988262538521 |
| `/db/HSFC` | Heat Source Functions | 35988291377305 |
| `/db/HAHS` | Heat source assignment | 35988378892441 |
| `/db/HPCE` | Pipe cooling | 35988420776345 |
| `/db/HSTG` | Heat-of-hydration construction-stage definition | 35988442589465 |

### 5-14. Settlement Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/SMPT` | Settlement Group | 35988516836633 |
| `/db/SMLC` | Settlement load case | 35988560566425 |

### 5-15. Miscellaneous Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/PLCB` | Pre-composite Section | 35988644139673 |
| `/db/LDSQ` | Load Sequence for Nonlinear | 35988663234329 |
| `/db/WVLD` | Wave Loads | 35988728179097 |
| `/db/IELC` | Ignore Elements for Load Cases | 35988790960921 |
| `/db/IFGS` | Large displacement - geometric-stiffness initial force | 35988857497113 |
| `/db/EFCT` | Small displacement - initial force control data | 35988927684633 |
| `/db/INMF` | Small displacement - initial element force | 35988975670937 |

### 5-16. Grid Model Analysis Loads
| Path | Role | article |
| --- | --- | --- |
| `/db/GALD` ᴶ | Grid Analysis Load | 39236719728281 |

### 5-17. Analysis (control data)
| Path | Role | article |
| --- | --- | --- |
| `/db/ACTL` | Main Control Data | 35409287717657 |
| `/db/ACTL-M1` ᴴˢ | Main Control Data (Hyper-S) | 56397306107545 |
| `/db/PDEL` | P-Delta analysis control | 35989163268249 |
| `/db/BUCK` | Buckling analysis control | 35989190592537 |
| `/db/EIGV` | Eigenvalue analysis control | 35989224565273 |
| `/db/EIGV-M1` ᴴˢ | Eigenvalue analysis control (Hyper-S) | 56375983487385 |
| `/db/HHCT` | Heat-of-hydration analysis control | 35989317531417 |
| `/db/HHCT-M1` ᴴˢ | Heat-of-hydration analysis control (Hyper-S) | 56399833703193 |
| `/db/MVCT` | Moving-load analysis control | 35989483364633 |
| `/db/MVCTch` / `MVCTid` / `MVCTbs` / `MVCTtr` | Moving-load analysis control - by code | 35989644995609 et al. |
| `/db/SMCT` | Settlement analysis control data | 35990184995481 |
| `/db/NLCT` | Nonlinear analysis control data | 35990229420441 |
| `/db/NLCT-M1` ᴴˢ | Nonlinear analysis control (Hyper-S) | 56506850582425 |
| `/db/STCT` | Construction-stage analysis control data | 35990281053465 |
| `/db/STCT-M1` ᴴˢ | Construction-stage analysis control (Hyper-S) | 57053813627673 |
| `/db/BCCT` | Boundary Change Assignment | 35736960800281 |
| `/db/BCGD-M1` ᴴˢ | Boundary Combination assignment (Hyper-S) | 56374180830745 |
| `/db/BCGA-M1` ᴴˢ | Boundary Combination assignment (Hyper-S) | 56375029084697 |

### 5-18. Analysis Results (combination/cutting)
| Path | Role | article |
| --- | --- | --- |
| `/db/LCOM-GEN` | Load combination - General | 35990806887065 |
| `/db/LCOM-CONC` | Load combination - Concrete Design | 35990864052249 |
| `/db/LCOM-STEEL` | Load combination - Steel Design | 35990929861913 |
| `/db/LCOM-SRC` | Load combination - SRC Design | 35991038731161 |
| `/db/LCOM-STLCOMP` | Load combination - Composite Steel Girder | 35991080923033 |
| `/db/LCOM-SEISMIC` | Load combination - Seismic Design | 35991142266265 |
| `/db/CUTL` | Cutting Line | 35991257189017 |
| `/db/CLWP` | Plate Cutting Line Diagram | 35991500289561 |

### 5-19. Bridge Specialization Results
| Path | Role | article |
| --- | --- | --- |
| `/db/GSBG` | Bridge Girder Diagrams | 35991591178265 |
| `/db/GCMB` | General Camber Control | 35991765204121 |
| `/db/CAMB` | FCM Camber Control | 35991862460697 |
| `/db/ULFC` | Cable Control - Unknown Load Factor | 35991960319897 |

### 5-20. Time History Analysis Results
| Path | Role | article |
| --- | --- | --- |
| `/db/THRE` | TH Graph - Element Force Smart Graph | 39236753314073 |
| `/db/THRG` | TH Graph - General Link Smart Graph | 35992341376025 |
| `/db/THRI` | TH Graph - Inelastic Hinge Smart Graph | 35992399685017 |
| `/db/THRS` | TH Graph - Seismic Devices Smart Graph | 35992460196121 |

### 5-21. Heat of Hydration Results
| Path | Role | article |
| --- | --- | --- |
| `/db/HHND` | Heat-of-hydration result graph | 35992577650841 |

### 5-22. Pushover
| Path | Role | article |
| --- | --- | --- |
| `/db/POGD` | Pushover analysis control data | 35992664632601 |
| `/db/POGD-M1` ᴴˢ | Pushover global control (Hyper-S) | 56511008007705 |
| `/db/IEPI` | Pushover elements ignoring initial load | 35992797619097 |
| `/db/PHGE` | Pushover hinge property assignment | 35992838417049 |
| `/db/POLC` | Pushover load case | 35993449470489 |
| `/db/POLC-M1` ᴴˢ | Pushover load case (Hyper-S) | 56506753403673 |

### 5-23. Design
| Path | Role | article |
| --- | --- | --- |
| `/db/DCON` | RC Design Code | 35993633394969 |
| `/db/MATD` | Concrete material modification | 35993732216985 |
| `/db/RCHK` | Rebar Input - Beam/Column | 35993850335897 |
| `/db/LENG` | Unbraced Length | 49513511154329 |
| `/db/MEMB` | Member Assignment | 49513603328793 |
| `/db/DCTL` | Definition of Frame | 49513652948377 |
| `/db/LTSR` | Limiting Slenderness Ratio | 49513681377689 |
| `/db/ULCT` | Underground Load Combination Type | 49513792356505 |
| `/db/MBTP` | Modify Member Type | 49513816193689 |
| `/db/WMAK` | Modify Wall Mark Design | 49513846785817 |
| `/db/DSTL` | Steel Design Code | 52149417728665 |

---

## 6. OPE — Operations `(/ope/...)`

GUI control / computing pre-processing values not stored in the DB.

| Endpoint | Role | article |
| --- | --- | --- |
| `/ope/PROJECTSTATUS` | Project status | 35994678976281 |
| `/ope/DIVIDEELEM` | Element division | 35994694310937 |
| `/ope/SECTPROP` | Section property calculation result | 35994769341081 |
| `/ope/USLC` | Using Load Combinations | 35994827741465 |
| `/ope/LINEBMLD` | Line Beam Load | 35994879160857 |
| `/ope/AUTOMESH` | Planar-region Auto-Mesh | 35736427971225 |
| `/ope/SSPS` | Surface Spring | 39772183634329 |
| `/ope/EDMP` | Change Property | 39772649347865 |
| `/ope/STOR` | Story Calculation | 49514653408793 |
| `/ope/STORY_PARAM` | Story Check Parameter | 49514705474457 |
| `/ope/STORY_IRR_PARAM` | Story Irregularity Check Parameter | 49514751862425 |
| `/ope/STORPROP` | Story Properties | 49514773501721 |
| `/ope/MEMB` | Member Assignment | 49514964272665 |

---

## 7. VIEW — Model view control `(/view/...)`

Each feature can be used independently. `CAPTURE` can be combined with `ANGLE`/`ACTIVE`/`DISPLAY`/`RESULTGRAPHIC`.

| Endpoint | Role | article |
| --- | --- | --- |
| `/view/SELECT` | Selection (response `SELECT` key) | 35995942911257 |
| `/view/CAPTURE` | Capture | 35996023805337 |
| `/view/PRECAPTURE` | Dialog capture | 39236964850329 |
| `/view/ANGLE` | Viewpoint | 35736247981209 |
| `/view/ACTIVE` | Active | 35523395368985 |
| `/view/DISPLAY` | Display | 35996157533977 |
| `/view/RESULTGRAPHIC` | Result graphic display (deformation/contour/stress/mode shape/heat-of-hydration, many) | 35996812786841 et al. |

---

## 8. POST — Table/design extraction `(/post/...)`

**POST only**, body is `"Argument"`.
- Use `/post/TABLE` to extract pre/post-processing **tables**. (Usage: [Designing with Intent: POST/TABLE](https://support.midasuser.com/hc/en-us/articles/45171987915929))
- The other `/post/{...}` are design-force / code-check-only endpoints.

### 8-1. Table types extracted via `/post/TABLE`
- **Pre-Process**: Element Weight, Nodal Body Force, Mass Summary, Load Summary, Material, Section,
  Restraint Supports, Story Mass/Load Summary, Story Weight.
- **Analysis Result**: Reaction(Local/Global/SurfaceSpring), Displacement(Local/Global), Truss Force/Stress,
  Cable Force/Configuration/Efficiency, Beam Force/Stress(+Equivalent/PSC/7DOF), Plate Force/Stress/Strain(Local/Global/UnitLength),
  Plane Stress·Plane Strain·Axisymmetric·Solid (Force/Stress/Strain), Elastic Link, General Link(Force/Deformation),
  Resultant Force, Vibration/Buckling Mode, Effective Span Length, Nodal Results of RS,
  Tendon (Coordinates/Elongation/Arrangement/Loss/Weight/StressLimit/ApproxLoss),
  Composite Section for C.S., Element/Beam Section Properties at Stage, Lack of Fit Force(Truss/Beam/Plate),
  Equilibrium Element Nodal Force, Initial Element Force, Wall Force/Moment.
- **Analysis Story**: Story Drift/Displacement/Shear(+Coefficient/Ratio)/Mode Shape/Eccentricity,
  Overturning Moment, Axial Force Sum, Stability Coefficient, Torsional Irregularity/Amplification,
  Stiffness·Capacity·Weight Irregularity, Criteria for Regularity in Plan, Ultimate Story Shear.
- **Time History Result**: Disp/Vel/Accel, Beam Force, Truss Force, General Link,
  Inelastic Hinge (Event Time/Summary/Force/Deformation/Element Rotation/Ductility Factor), Fiber Section ᴶ (several).
- **Heat of Hydration Result**: Stress, Temperature, Displacement, Tensile Stress, Pipe Cooling Nodal Temp.
- **TH Text / Pushover Text**: Node·Element(Truss/Beam/Plate/Wall)·General/Elastic Link result text.

> All the tables above are called via `POST /post/TABLE` + `Argument` (table type, range, etc.).
> For the exact Argument schema, see the POST/TABLE guide and each table's article → once confirmed, reflect it into `midas-api-examples.json`.

### 8-2. Design-force / code-check endpoints
| Endpoint | Role | article |
| --- | --- | --- |
| `/post/PM` | P-M interaction diagram | 36021337973017 |
| `/post/STEELCODECHECK` | Steel code check | 44662732910233 |
| `/post/BEAMDESIGNFORCES` | Concrete Design - Beam Design Force | 49514295460889 |
| `/post/COLUMNDESIGNFORCES` | Concrete Design - Column Design Forces | 49514320078489 |
| `/post/BRACEDESIGNFORCES` | Concrete Design - Brace Design Forces | 49514395318041 |
| `/post/WALLDESIGNFORCES` | Concrete Design - Wall Design Forces | 49514433321881 |
| `/post/STEELMEMBERDESIGNFORCES` | Steel Design - Member Design Forces | 49514496461593 |
| `/post/SRCBEAMDESIGNFORCES` | SRC Design - Beam Design Forces | 49514560567961 |
| `/post/SRCCOLUMNDESIGNFORCES` | SRC Design - Column Design Forces | 49514609393049 |
| `/post/COLDFORMEDSTEELMEMBERDESIGNFORCES` | Cold Formed Steel - Member Design Forces | 49514621265305 |

---

## 9. This template's mapping (`utils_api.ts`)

| utils_api function | method · path | body |
| --- | --- | --- |
| `dbRead(item)` | `GET /db/{item}` | — (unwraps the `item` key from the response) |
| `dbReadItem(item, id)` | `GET /db/{item}/{id}` | — |
| `dbCreate(item, items)` | `POST /db/{item}` | `{ Assign: items }` |
| `dbCreateItem(item, id, x)` | `POST /db/{item}/{id}` | `{ Assign: x }` |
| `dbUpdate(item, items)` | `PUT /db/{item}` | `{ Assign: items }` |
| `dbUpdateItem(item, id, x)` | `PUT /db/{item}/{id}` | `{ Assign: x }` |
| `dbDelete(item, id)` | `DELETE /db/{item}/{id}` | — |

- If you need DOC/OPE/VIEW/POST, add a function via `requestJson(method, endpoint, body)` in `utils_api.ts`.
  e.g. `requestJson("POST", "/doc/ANAL", {})`, `requestJson("GET", "/view/SELECT")`,
  `requestJson("POST", "/post/TABLE", { Argument: {...} })`.

---

## 10. Gotchas (only things not in the catalog/conventions)

- Marker endpoints `ᴴˢ` (Hyper-S solver only) / `ᴶ` (JP version only) work only in the matching environment.
- Request precondition: the target **NX app is running + a file is open** (fails otherwise).
- The exact field names and required values are ultimately grounded in the relevant **article page**. Once confirmed, reflect into `midas-api-examples.json`.

### 10-1. `db/IEHP` — Inelastic hinge "property definition" (undocumented endpoint)

- The DB holding the actual property data (skeleton curve / hysteresis model) that `IEHG` (Assign) and `NLNK`'s `IEHP_NAME` merely **reference by name**. In the manual it exists only as the GUI "Define Inelastic Hinge Properties" dialog with **no Open API URI, yet `GET/PUT {base}/db/IEHP` works** (verified live on `civil`, 2026-07).
- Record structure (key fields): `NAME`, `DESC`, `DEFINITION` (e.g. `SKEL` = skeleton), `HINGE_TYPE` (e.g. `DIST` = distributed), `INTERACTION_TYPE` (e.g. `NONE`), `MATERIAL_TYPE` (e.g. `RC`), `LOCATION` (`I`), `USEIEHCLOCATION`, `COMPONENT_DIR` (6-DOF bool array [Fx,Fy,Fz,Mx,My,Mz]), `HYSTERESIS_MODEL` (string array, length 9), `SECTION_NUM`, `EXIST_IJ_PROP`, `ALL_PROP` (length 8) / `ALL_SUBPROP` (length 8), `MULT_DATA`.
- **⚠️ Read rule (important)**: `ALL_PROP[i]` is a **union object** — only components where `COMPONENT_DIR[i] == true` (active) hold valid values. **Inactive components serialize uninitialized memory as-is**, producing **garbage values** like `-1717986918` or `5.29e-315`. Always filter by `COMPONENT_DIR` when reading.
- The single key inside `ALL_PROP[i]` corresponds to that component's `HYSTERESIS_MODEL[i]`: `ETR→ELATRI` (elasto-plastic tri-linear), `KIN→KINEMA` (kinematic), `TAK→TAKEDA`. Its `COMPONENTPROPS` holds the real M-φ values: `CRACK/YIELD/ULTIMATE MOMENT`, `YIELDROTN1/2/3RD`, `STIFFRATIO`, `DEFORMCAPACITY`, etc.
- `ALL_SUBPROP[i]` is the member's J-end value (when both ends differ). If `EXIST_IJ_PROP` is all false, it equals the I-end (`ALL_PROP`).
- Note: payload can be large (e.g. 362 records ≈ 4.2 MB in a real model). Alternative paths: GUI CSV Export, or a full-model round-trip via `/doc/EXPORT` (JSON).
- Example payload: see the `IEHP` key in [`./midas-api-examples.json`](./midas-api-examples.json).

### 10-2. `db/SECT` `SECTTYPE:"VALUE"` — numeric (直接入力) section (schema not in the article)

The article (35808653964185) lists `VALUE` as a `SECTTYPE` but **does not document the numeric property fields**. Confirmed by **live round-trip** (GUI-create → `GET /db/SECT` → `POST` reproduce, 201 OK, `civil`, 2026-07):

- **The section is stored under `SECT_BEFORE.SECT_I`**, and two things make the server treat it as a numeric section instead of a dimensioned shape:
  - **`BUILT_FLAG: 1`** and a **`STIFF: { ... }`** object holding the properties directly.
  - **`vSIZE` is all zeros** (`[0,0,0,0,0,0,0,0]`), `SHAPE` is display-only (`"SB"` etc.).
- **⚠️ Failure cause**: omitting `BUILT_FLAG`/`STIFF` (i.e. sending `SHAPE` + `vSIZE` like a DB/User section) makes the server **validate `vSIZE` as real dimensions** → rejects with **`断面寸法が誤って入力されました`** (“section dimensions entered incorrectly”). This is the trap; VALUE ≠ dimensioned.
- **`STIFF` field map**: `AREA`=area A · `ASY`/`ASZ`=effective shear areas · `RXX`=torsional constant **J (Ixx)** · `RYY`=**Iyy** · `RZZ`=**Izz** · `CYP`/`CYM`/`CZP`/`CZM`=extreme-fiber distances for stress (±y, ±z) · `QYB`/`QZB`=shear-stress factors Q/b · `Y[4]`/`Z[4]`=4 stress-point coordinates.
- **`DESIGN`** (optional on POST, server fills 0): `YBAR`/`ZBAR`=centroid · `ZYY`/`ZZZ`=section moduli. `PERIIN`/`PERIOUT`=inner/outer perimeter.
- Because you supply **exact J, Asy, Asz** directly, VALUE preserves torsion/shear constants perfectly — the correct choice when a rectangle-shape + scale-factor workaround would distort dynamic-analysis results.
- Example payload: see key `"6003"` under the `SECT` example in [`./midas-api-examples.json`](./midas-api-examples.json).
