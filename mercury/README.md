# Mercury

A Tkinter desktop editor for the project's binary O₂/N₂ membrane model. Build an
acyclic flowsheet, edit equipment, run it, and inspect product specifications.

## Launch

From the project root:

```bash
.venv/bin/python -m mercury
```

Mercury uses the existing NumPy, SciPy, and pandas environment. For a new Python
environment, install `mercury/requirements.txt`. Tkinter is an **OS Python
binding**, not a pip dependency. Install the binding for the interpreter you use:

```bash
# Fedora
sudo dnf install python3-tkinter

# Debian / Ubuntu
sudo apt install python3-tk
```

Run from a graphical desktop session. The launcher reports missing Tk bindings or
an unavailable display rather than starting an unusable window. The engine can
be imported and tested without Tkinter or a display.

For this development workspace, matching Fedora 44 / Python 3.14 Tk packages
were extracted into the ignored `mercury/.runtime/` directory because system
installation required a sudo password. The launcher prefers system Tk and uses
these local bindings only if system Tk cannot be imported. This directory is a
local development dependency, not a portable or committed distribution. No
downloads or package installation occur at application startup.

## Using the editor

- The initial design is **Feed → compressor → O → O → O → N**. The first
  membrane's permeate is the oxygen product, final retentate is the nitrogen
  product, and the remaining permeates terminate at vents.
- Choose a starting membrane preset beside **Add**, then click **Membrane**.
  Other equipment buttons add compressors, splitters, mixers, and feeds.
  **Right-click empty workspace** to place equipment at that location, including
  any built-in or custom membrane preset.
- Drag the body of a block to move it. Its full name, parameters, and stage-cut
  annotation sit **outside the box**; drag that label independently. Select a
  block and drag its blue bottom-right handle to resize it, or enter **Box width**
  and **Box height** in Properties (minimum 100 × 64 diagram units). Long names
  wrap in their own annotation instead of overflowing the equipment box.
- **Fit diagram**, **+**, **−**, and Ctrl+mouse wheel control zoom; scrollbars and
  middle-button dragging pan. Diagram, Properties, and Products/Validation panes
  start visible and enforce minimum sizes. Drag their dividers to adjust them;
  **Reset view** restores pane sizes and fits the diagram.
- Click a **filled output port**, then an **open input port** to connect them.
  A membrane's right port is retentate and its bottom port is permeate.
  Ports have enlarged click targets at every zoom. Splitter and mixer ports are
  labeled A/B, membrane ports R/P. Esc or right-click cancels a pending
  connection. Each port accepts one connection; use splitters/mixers explicitly.
- Select a block to edit its inputs, then **Apply changes**. Run, Validate, and
  Save also apply pending edits to the selected object. Changing simulation inputs
  clears old results. Moving blocks changes only layout and keeps valid results.
- Set **Assumed temperature (°C)** above the diagram, then click **Apply
  temperature** or press Enter. Any finite value above −273.15 °C is accepted,
  including decimals such as 21.5. Run, Validate, and Save also apply this input.
  It sets every stream's assumed temperature and the inlet temperature used for
  ideal compressor power. The default is 21 °C; the setting is saved per flowsheet.
- Select a stream to rename it or inspect temperature, pressure (psia/psig), flow
  (slpm), and O₂ composition (mole fraction and percentage). The four **Stream
  labels** checkboxes independently show/hide temperature, pressure, flow, and O₂
  on all arrows, with no empty rows when a metric is hidden. Numeric values sit in
  Aspen-style outlines: **capsule = temperature**, **hexagon = pressure**,
  **bow-tie = flow**, and **rectangle = O₂ mole fraction**. Each stream name appears
  once below its badges; long names are shortened on the diagram and remain fully
  available in Properties. Before Run, uncalculated values show “—”; temperature
  shows the selected assumption. Pressure uses two decimals, flow whole slpm,
  and O₂ four decimals.
- The **draggable stream legend** explains °C, psia, slpm, and O₂ mol/mol, including
  the selected temperature assumption. Its saved position starts above the default network;
  right-click it for **Reset legend position**. Fit diagram includes the legend.
  A small warning triangle marks a product specification failure. Detailed outlet
  roles, units, assumptions, and specification results remain in Properties and
  the product table instead of being repeated beside every arrow.
- **Right-click empty workspace → Add textbox** creates a note at that position
  and focuses its multiline editor in Properties. Set its width, positive integer
  font size, bold style, and text color (the picker or a `#RRGGBB` value), then
  **Apply changes**. The defaults are 240 diagram units wide, 11-point regular text,
  and dark blue-gray. Drag the text to move it or its selected corner handle to
  adjust wrapping width; height follows the content. The background is transparent
  and the boundary is shown only while selected. Empty notes show `[Text]` as an
  editing placeholder. Double-click to edit, or use its context menu. Delete
  removes a selected textbox; inside its text editor, Delete edits text normally.
  Notes and legend edits keep existing simulation results.
- Left-clicking anywhere outside a context menu closes it and performs the
  clicked control's normal action. Submenus remain interactive. Escape, choosing
  a command, opening another context menu, or replacing the flowsheet also closes
  the active menu.
- **Right-click an unconnected output port** to create an oxygen product,
  nitrogen product, collected/report-only stream, or purge/vent. These are
  **arrows ending in free space**, with no terminal equipment box. Their role is
  a property of the stream; change it through its right-click **Outlet** submenu
  or Properties → Inputs → Outlet role. Products are evaluated individually;
  use a mixer if several streams form one product. All outlets, including purges,
  remain in the material balances. The results table selects the corresponding
  terminal stream when clicked.
- **Select a stream to edit its path**. Drag square bend handles, round segment
  handles (moves the segment sideways), or a terminal arrow's larger round end
  handle. Route edits snap to a 10-unit diagram grid; attached ports continue to
  follow equipment. Double-click the path or use **Add bend here** on its context
  menu to insert a bend; right-click a bend to remove it. **Reset automatic route**
  removes manual bends. Drag the stream's label independently, or reset its label
  position from the context menu. Layout edits keep valid simulation results.
- Right-click an equipment-to-equipment stream and choose **Disconnect downstream
  → outlet** to turn it into a product/purge. Downstream equipment remains in the
  diagram, and Validate reports its now-unconnected inlet. To reconnect a terminal
  outlet to equipment, delete its stream and connect the newly open output port.
- **Validate** lists structural/input errors. Click a message to select its
  equipment or stream. Dynamic pressure compatibility and numerical checks occur
  during **Run Simulation**. Errors do not produce an apparently successful result.
- **Run Simulation** uses a background worker. Equipment edits are disabled while
  it runs; the GUI remains responsive. Select equipment afterward to inspect
  inlet/outlet conditions, stage cut, and ideal compressor power.
- **Reset Results** clears calculations only. **Clear** removes the flowsheet.
  **Restore Default** reloads the OOON design with current built-in presets.
- **Delete Selected** or the Delete key removes a selected block/connection.
  Deleting a block also deletes its attached connections. Delete while editing
  a text field edits that field instead.
- **Save as preset** saves the selected membrane's edited values in
  `mercury/user_presets.json`. O and N cannot be overwritten. Existing nodes keep
  their property snapshots when a custom preset is changed.
- **Open**, **Save**, and **Save as** use versioned JSON. Equipment, full property
  snapshots, positions, box sizes, label offsets, stream bends, terminal endpoints,
  connections, outlet roles, formatted textboxes, temperature assumption, and the legend position are
  saved in **version 3**. Versions 1 and 2 load with no textboxes and a default
  legend position, preserving the existing diagram layout. Version 1 files load
  automatically: connected product/vent boxes become terminal arrows with their
  names, roles, and endpoints preserved. Unconnected legacy sink placeholders
  remain as labeled arrow markers so unfinished designs lose no inputs; attach
  their open input port to complete the conversion. Calculated
  results are never saved as authoritative inputs. Structurally valid unfinished
  designs may be saved and reopened; missing connections or cycles are then shown
  by validation. Malformed files do not replace the current workspace.
  Files without a temperature setting load with the 21 °C default.

Keyboard shortcuts: **Ctrl+R** run, **Ctrl+S** save, **Ctrl+O** open.

## Compressor controls

The selected RPM chooses the existing compressor curve. `inlet` control follows
the actual incoming flow and calculates discharge pressure. `flow` requests a
flow; `pressure` requests an absolute outlet pressure and inverts the curve.
The property panel previews the relationship when the inlet is known.

For a **directly connected feed**, Apply keeps both objects consistent:

- Compressor flow edits update feed flow.
- Compressor outlet-pressure edits calculate and update feed flow.
- RPM or feed-pressure changes preserve the active flow/pressure control mode.
- Editing feed flow switches the compressor to flow control.

A **downstream compressor** never changes upstream flows. It follows incoming flow
by default; a requested flow/pressure must agree with that inlet and the curve.
Otherwise, Run reports the mismatch and identifies the compressor. Values outside
the measured map are rejected; no extrapolation is used.

## Engineering basis and development notes

The membrane mathematics were extracted unchanged from `membrane_stage` in
`membrane-data.ipynb`: the four perfect-mixing equations, initial guess, physical
bounds, and SciPy least-squares settings are preserved. `simulation/membrane.py`
contains that callable alone. `simulation/numerics.py` wraps it with the same
physical and residual checks used by the notebook's network sweeps.

The notebook's `compressor` and `ideal_compressor_power` methods were adapted only
to remove reliance on notebook globals. The compressor dataframe is supplied
explicitly. `data/comp_data.csv` remains the map source: numeric RPM columns are
pressure ratios versus standard flow; the separate `P_out` column is not used.
Inverse interpolation reverses each strictly decreasing ratio curve. The valid
flow limits are 0–15,000, 0–17,000, and 0–19,000 slpm for 2500, 3500, and 4500 rpm.

`simulation/presets.py` centralizes presets. O has 4000 m² area, 0.031
slpm/(psi·m²) O₂ permeance, and selectivity 5.5. N has 2500 m² area and is fitted
from `data/permeance_data_collection.csv` using the notebook's regression:
oxygen replicates 1/2, nitrogen replicate 1, pilot area 9.86 m², and conversion
0.471947 slpm/scfh. The current fit gives O₂ permeance 0.018390162805594107 and
selectivity 5.976608187134502. The older hard-coded values in the standalone
simulator are not used as the new-module preset. Calibration is read at startup;
saved flowsheets retain their own property snapshots.

Key assumptions:

- Every stream uses the user-defined **assumed temperature** (default 21 °C),
  exposed by `Flowsheet.temperature_c` and `Stream.temperature_c`. No temperature
  solver, thermal mixing, or compressor heat balance is implied. Membrane
  permeance, flow, and composition equations do not depend on this setting.
- Standard flow is slpm throughout; the ideal-power calculation retains the
  existing 22.414 L/mol basis and gamma 1.4. Its inlet temperature is the chosen
  Celsius assumption plus 273.15 K. Setting 25 °C reproduces the previous
  298.15 K power basis. Reported
  power is ideal isentropic power, not actual motor power. No energy balance is
  introduced for compressor outlet temperature.
- Internal pressure is **psia**. The editor shows the corresponding psig using
  14.7 psia atmosphere. Membrane retentate pressure equals inlet pressure;
  permeate pressure defaults to 14.7 psia. No retentate pressure drop is assumed.
- Mixing is flow-weighted. Mixer pressures must agree within relative/absolute
  tolerance `1e-6`; otherwise equalization equipment would be required and the
  simulation rejects the mixer. Splitters change flow only.
- Composition is a mapping with O₂/N₂ mole fractions. The model currently allows
  only those two species. Percentages are a GUI presentation convention.
- Stream flow can be zero, but a membrane requires positive inlet flow because
  its equations normalize by flow. A mixer requires positive combined flow.
- Numerical acceptance requires finite physical solutions and maximum normalized
  equation/balance residual `1e-6`. A converged optimizer with an unacceptable
  residual is reported as a numerical failure. External balances include vents.
- Stage cut is calculated, not an independent input. Values outside 0.20–0.40
  generate warnings rather than preventing a valid calculation.
- Product targets are inclusive, matching the notebook: oxygen product
  **≥3400 slpm and ≥40% O₂**, nitrogen product **≥6000 slpm and ≤5% O₂**.
  Product-spec failures are evaluation results, not solver failures.
- Recycles, implicit splitting/merging, automatic pressure equalization, and
  automatic upstream adjustment for downstream compressors are outside this MVP.

Tkinter/ttk was selected to match the requested installed framework and avoid a
Qt/web dependency. Canvas graphics represent model objects; they do not hold the
only copy of properties. The original notebook and standalone simulator are not
executed at runtime or modified by Mercury.

## Headless API

```python
from mercury.simulation import CompressorMap, PresetStore, default_flowsheet

curves = CompressorMap()
sheet = default_flowsheet(PresetStore())
sheet.temperature_c = 25.0
issues = sheet.validate(curves)
result = sheet.simulate(curves)
for product in result.products.values():
    print(product.name, product.stream.flow_slpm, product.stream.oxygen, product.passed)
```

`Flowsheet` exposes `add`, `connect`, `delete`, `update_properties`,
`topological_order`, `validate`, `simulate`, `save`, and `load`. Editing through
`update_properties` applies direct-feed/compressor linking transactionally;
failed edits leave the flowsheet unchanged. `simulate` does not modify inputs.

A terminal stream is `Connection("P-1", "Oxygen product", "M-1", "permeate",
outlet="oxygen")`: `target_node` and `target_port` are `None`. Valid outlet values
are `oxygen`, `nitrogen`, `report`, and `vent`. Results for terminal products/vents
are keyed by **connection ID**, so the default products are `P-1` and `R-4`.
`endpoint`, `waypoints`, `label_dx`, and `label_dy` are diagram geometry in unscaled
coordinates. Legacy product/vent equipment remains accepted by the headless API;
loading a saved design converts connected legacy sinks to terminal streams.

Diagram notes use `TextAnnotation(id, text, x, y, width=240, font_size=11,
bold=False, color="#243b55")`, added through `sheet.add_annotation(...)` and stored
in `sheet.annotations`. Their IDs share the equipment/connection namespace.
`sheet.legend` is a `LegendPosition(x, y)`; `sheet.reset_legend_position()` places
it above the network again. Both dataclasses are exported by `mercury.simulation`
and require no Tkinter. Annotations never participate in graph ordering or material
balances. Serialization validates their types, finite geometry, positive sizes,
and six-digit hex colors; calculated results are still excluded from saved files.

The current default produces approximately:

| Quantity | Value |
|---|---:|
| Compressor discharge | 123.627 psia |
| Oxygen-product flow | 3737.0004 slpm |
| Oxygen-product O₂ | 41.9026% |
| Nitrogen-product flow | 6558.1841 slpm |
| Nitrogen-product O₂ | 4.8155% |
| Ideal compressor power (21 °C) | 93.2823 kW |
| New-module stage cut | 0.12913, advisory warning |

## Tests

```bash
.venv/bin/python -m unittest discover -s mercury/tests -v
```

Backend tests use standard-library `unittest` and do not need a display. They
cover interpolation/inversion, presets, streams, linked controls, graph order,
series and branch balances, failures, product limits, and JSON persistence.
Regression tests extract only the existing notebook function definitions into an
isolated namespace; they never run its sweeps, plotting, or CSV exports.

GUI tests use a real or virtual display and explicitly skip if Tk/display access
is unavailable. They exercise actual Canvas events, property forms, worker
responsiveness, result inspection, presets, and save/load. For example, with Xvfb
installed, run the suite under `xvfb-run`. Development acceptance was also run
against the locally extracted Xvfb with `DISPLAY=:97`.
The suite also covers individual label toggles, pane visibility at 1050×740 and
1500×940, enlarged port targets, context-menu creation and outlet changes, actual
label/resize/bend/segment/endpoint drag events, layout persistence, version 1
migration, terminal-stream balances, and unchanged notebook regression values.
Compact-badge tests check values, shape/text containment at multiple zooms, toggle
behavior, and warning symbols. Annotation tests cover formatting, actual drag and
resize events, focus/deletion behavior, legend placement, version 3 round trips,
and version 1/2 loading. Posted-menu tests exercise outside clicks, submenus,
single command dispatch, Escape, replacement, and binding cleanup on a real Tk
event loop.
