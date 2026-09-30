"""Application controller: model editing, worker execution and view coordination."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import logging
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from mercury.simulation import (
    CompressorMap, CompressorProperties, Connection, EmptyProperties, Equipment,
    FeedProperties, Flowsheet, PresetStore, ProductProperties, SplitterProperties, default_flowsheet,
)
from mercury.simulation.flowsheet import SimulationError, ValidationIssue
from mercury.simulation.models import SimulationResult, validate_properties, TextAnnotation, LEGEND_ID
from mercury.simulation.numerics import PROJECT_ROOT
from mercury.simulation.units import psia_to_psig, validate_temperature_c
from .property_editor import PropertyEditor
from .results_panel import ResultsPanel
from .workspace import Workspace, OUTLET_LABELS
from .context_menus import ContextMenus


class MainWindow:
    def __init__(self, root: tk.Tk, presets: PresetStore | None = None,
                 compressor_map: CompressorMap | None = None):
        self.root = root
        self.presets = presets or PresetStore()
        self.compressor_map = compressor_map or CompressorMap()
        self.sheet = default_flowsheet(self.presets)
        self.result: SimulationResult | None = None
        self.selected: str | None = None
        self.file_path: Path | None = None
        self.dirty = False
        self.busy = False
        self.closed = False
        self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='mercury-simulation')
        self.future = None
        self._poll_id = None
        self.buttons = []
        self.root.title('Mercury · Membrane flowsheets')
        width=min(1500,max(1050,self.root.winfo_screenwidth()-60))
        height=min(940,max(740,self.root.winfo_screenheight()-80))
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(1050,740)
        style=ttk.Style(root)
        if 'clam' in style.theme_names(): style.theme_use('clam')
        style.configure('.',font=('TkDefaultFont',10))
        style.configure('TFrame',background='#ffffff')
        style.configure('TLabel',background='#ffffff',foreground='#243b55')
        style.configure('Section.TLabel',font=('TkDefaultFont',12,'bold'))
        style.configure('Brand.TLabel',font=('TkDefaultFont',19,'bold'),foreground='#203e64')
        style.configure('TButton',padding=(9,6))
        style.configure('Treeview',rowheight=27)
        self._build()
        self.context_menus = ContextMenus(self.root)
        self.workspace.set_model(self.sheet)
        self.editor.show(None,None)
        self._layout_ready = False
        self._layout_job = self.root.after_idle(self._initialize_layout)
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.bind('<Control-r>',lambda event: self.run_simulation())
        self.root.bind('<Control-s>',lambda event: self.save())
        self.root.bind('<Control-o>',lambda event: self.load())
        self.root.bind('<Delete>',self._delete_key)
        if self.presets.warnings:
            self.show_issues([ValidationIssue(text) for text in self.presets.warnings])

    def _button(self,parent,text,command):
        button=ttk.Button(parent,text=text,command=command)
        button.pack(side='left',padx=(0,6))
        self.buttons.append(button)
        return button

    def _build(self):
        shell=ttk.Frame(self.root,padding=14)
        shell.pack(fill='both',expand=True)
        header=ttk.Frame(shell)
        header.pack(fill='x',pady=(0,10))
        ttk.Label(header,text='MERCURY',style='Brand.TLabel').pack(side='left')
        ttk.Label(header,text='  /  Membrane flowsheets',foreground='#64758a').pack(side='left',padx=10)
        files=ttk.Frame(header); files.pack(side='right')
        self._button(files,'Open…',self.load)
        self._button(files,'Save',self.save)
        self._button(files,'Save as…',lambda: self.save(save_as=True))
        equipment=ttk.Frame(shell)
        equipment.pack(fill='x',pady=(0,8))
        ttk.Label(equipment,text='Add',style='Section.TLabel').pack(side='left',padx=(0,10))
        self.preset_choice=tk.StringVar(value='O')
        self.preset_combo=ttk.Combobox(equipment,textvariable=self.preset_choice,values=self.presets.names,
                                     state='readonly',width=12)
        self.preset_combo.pack(side='left',padx=(0,6))
        for label,kind in [('Membrane','membrane'),('Compressor','compressor'),('Splitter','splitter'),
                           ('Mixer','mixer'),('Feed','feed')]:
            self._button(equipment,label,lambda k=kind:self.add_equipment(k))
        operations=ttk.Frame(shell)
        operations.pack(fill='x',pady=(0,10))
        self._button(operations,'Validate',self.validate)
        self.run_button=self._button(operations,'▶ Run Simulation',self.run_simulation)
        self._button(operations,'Reset Results',self.reset_results)
        self._button(operations,'Delete Selected',self.delete_selected)
        self._button(operations,'Clear',self.clear)
        self._button(operations,'Restore Default',self.restore_default)
        assumptions=ttk.Frame(shell)
        assumptions.pack(fill='x',pady=(0,8))
        ttk.Label(assumptions,text='Assumed temperature (°C):').pack(side='left',padx=(0,8))
        self.temperature_var=tk.StringVar(value=str(self.sheet.temperature_c).removesuffix('.0'))
        self.temperature_entry=ttk.Entry(assumptions,textvariable=self.temperature_var,width=10)
        self.temperature_entry.pack(side='left',padx=(0,8))
        self.temperature_entry.bind('<Return>',lambda event:self.apply_temperature())
        self._button(assumptions,'Apply temperature',self.apply_temperature)
        ttk.Label(assumptions,text='All streams and compressor inlet power.',foreground='#64758a').pack(side='left')
        view=ttk.Frame(shell)
        view.pack(fill='x',pady=(0,10))
        ttk.Label(view,text='Stream labels:').pack(side='left',padx=(0,8))
        self.label_vars={key:tk.BooleanVar(value=True) for key in ('temperature','pressure','flow','oxygen')}
        for key,label in [('temperature','Temperature'),('pressure','Pressure'),('flow','Flow'),('oxygen','O₂ fraction')]:
            ttk.Checkbutton(view,text=label,variable=self.label_vars[key],command=self.toggle_labels).pack(side='left',padx=(0,10))
        ttk.Button(view,text='Reset view',command=self.reset_view).pack(side='right')
        ttk.Button(view,text='Fit diagram',command=lambda:self.workspace.fit()).pack(side='right',padx=4)
        ttk.Button(view,text='+',width=3,command=lambda:self.workspace.zoom(1.2)).pack(side='right')
        ttk.Button(view,text='−',width=3,command=lambda:self.workspace.zoom(1/1.2)).pack(side='right',padx=4)
        # Native panes support minimum visible sizes; explicit requests prevent
        # the inspector's content size from consuming the diagram at startup.
        panes=self.panes=tk.PanedWindow(shell,orient='vertical',sashwidth=9,
                                        background='#dce3ed',borderwidth=0,opaqueresize=True)
        panes.pack(fill='both',expand=True)
        top=self.top_panes=tk.PanedWindow(panes,orient='horizontal',sashwidth=9,
                                          background='#dce3ed',borderwidth=0,opaqueresize=True)
        left=self.diagram_pane=ttk.Frame(top)
        ttk.Label(left,text='Right-click to add / route  ·  Drag labels or handles  ·  Middle-drag to pan',
                  foreground='#66768a').pack(anchor='w',pady=(0,6))
        self.workspace=Workspace(left,self.select,self.connect_ports,self.move_node,self.set_status)
        self.workspace.on_geometry=self.edit_geometry
        self.workspace.on_context=self.context_menu
        self.workspace.on_edit_annotation=self.edit_annotation
        self.workspace.pack(fill='both',expand=True)
        self.editor=PropertyEditor(top,self.presets,self.compressor_map,self.apply_selected,self.save_preset,self.compressor_preview)
        top.add(left,minsize=480,width=1000,stretch='always')
        top.add(self.editor,minsize=330,width=365,stretch='never')
        panes.add(top,minsize=300,height=550,stretch='always')
        self.results_panel=ResultsPanel(panes,self.select)
        panes.add(self.results_panel,minsize=155,height=205,stretch='never')
        self.status=tk.StringVar(value='Default OOON design loaded. Click Run Simulation to calculate results.')
        ttk.Label(shell,textvariable=self.status,foreground='#4d637b',wraplength=1400).pack(fill='x',pady=(10,0))

    def _initialize_layout(self):
        self._layout_job=None
        if self.closed: return
        if not self.panes.winfo_ismapped() or self.panes.winfo_height()<400 or self.top_panes.winfo_width()<800:
            self._layout_job=self.root.after(50,self._initialize_layout)
            return
        self.reset_view()
        self._layout_ready=True

    def reset_view(self):
        self.root.update_idletasks()
        self.top_panes.sash_place(0,max(480,self.top_panes.winfo_width()-374),0)
        self.panes.sash_place(0,0,max(300,self.panes.winfo_height()-214))
        self.root.update_idletasks()
        self.workspace.fit()

    def set_status(self,text):
        self.status.set(text)

    def toggle_labels(self):
        self.workspace.label_fields={key for key,var in self.label_vars.items() if var.get()}
        self.workspace.draw()

    def _title(self):
        name=self.file_path.name if self.file_path else self.sheet.name
        self.root.title(f'Mercury · {name}{" *" if self.dirty else ""}')

    def _refresh(self):
        self.workspace.selected=self.selected
        self.workspace.set_model(self.sheet,self.result)
        obj=self.selected_object()
        self.editor.show(obj,self.result)
        self._title()

    def select(self,object_id):
        self.selected=object_id
        self.workspace.selected=object_id
        self.workspace.draw()
        obj=self.selected_object()
        self.editor.show(obj,self.result)

    def selected_object(self):
        if self.selected == LEGEND_ID: return self.sheet.legend
        return (self.sheet.nodes.get(self.selected) or self.sheet.connections.get(self.selected)
                or self.sheet.annotations.get(self.selected))

    def _invalidate(self):
        self.result=None
        self.workspace.result=None
        self.workspace.error_nodes.clear()
        self.workspace.error_connections.clear()
        self.results_panel.clear()
        self.dirty=True
        self.set_status('Inputs changed. Run Simulation to update calculated results.')

    def _next_id(self,prefix):
        used=self.sheet.object_ids()
        i=1
        while f'{prefix}-{i}' in used: i+=1
        return f'{prefix}-{i}'

    def add_equipment(self,kind,position=None,preset=None):
        if self.busy: return
        defaults={'feed':FeedProperties(),'compressor':CompressorProperties(),
                  'splitter':SplitterProperties(),'mixer':EmptyProperties(),
                  'product':ProductProperties(),'vent':EmptyProperties()}
        prefixes={'feed':'F','compressor':'C','membrane':'M','splitter':'SP','mixer':'MX','product':'PR','vent':'V'}
        p=self.presets.get(preset or self.preset_choice.get()) if kind=='membrane' else defaults[kind]
        key=self._next_id(prefixes[kind])
        x=(self.workspace.canvas.canvasx(80))/self.workspace.scale
        y=(self.workspace.canvas.canvasy(100))/self.workspace.scale
        if position is not None: x,y=position
        occupied={(round(n.x),round(n.y)) for n in self.sheet.nodes.values()}
        while (round(x),round(y)) in occupied: x+=30; y+=30
        self.sheet.add(Equipment(key,key,kind,p,x,y))
        self.selected=key
        self._invalidate(); self._refresh()

    def connect_ports(self,source,target):
        if self.busy: return
        try:
            draft=deepcopy(self.sheet)
            key=self._next_id('S')
            draft.connect(Connection(key,key,source[0],source[1],target[0],target[1]))
            node=draft.nodes[target[0]]
            if node.kind=='compressor' and draft.direct_feed(node.id):
                draft.update_properties(node.id,node.properties,self.compressor_map)
            draft.migrate_terminal_nodes()
            self.sheet=draft
            self.selected=key
            self._invalidate(); self._refresh()
            self.set_status(f'Connected {source[0]} / {source[1]} → {target[0]} / {target[1]}.')
        except (ValueError,TypeError) as error:
            self.show_issues([ValidationIssue(str(error),target[0])])

    def move_node(self,key,x,y):
        if self.busy: return
        self.sheet.nodes[key].x,self.sheet.nodes[key].y=x,y
        self.dirty=True
        self._title()

    def edit_geometry(self,key,**changes):
        if self.busy: return
        if key == LEGEND_ID:
            self.sheet.legend=replace(self.sheet.legend,**changes)
        elif key in self.sheet.annotations:
            self.sheet.annotations[key]=replace(self.sheet.annotations[key],**changes)
        elif key in self.sheet.nodes:
            node=replace(self.sheet.nodes[key],**changes)
            validate_properties(node)
            self.sheet.nodes[key]=node
        else:
            self.sheet.connections[key]=replace(self.sheet.connections[key],**changes)
        self.dirty=True
        self._title()

    def add_textbox(self,position):
        if self.busy: return
        key=self._next_id('TXT')
        self.sheet.add_annotation(TextAnnotation(key,'Text',*position))
        self.dirty=True
        self.edit_annotation(key)
        self._title()

    def edit_annotation(self,key):
        if self.busy: return
        self.select(key)
        self.editor.focus_text()

    def reset_legend(self):
        if self.busy: return
        self.sheet.reset_legend_position()
        self.dirty=True
        self._refresh()

    def create_outlet(self,source,outlet):
        if self.busy: return
        key=self._next_id('P' if outlet!='vent' else 'V')
        self.sheet.connect(Connection(key,OUTLET_LABELS[outlet],*source,outlet=outlet))
        self.selected=key
        self._invalidate(); self._refresh()
        self.set_status('Terminal stream created. Drag its round endpoint or label; right-click to change collection role.')

    def set_outlet(self,key,outlet):
        if self.busy: return
        edge=self.sheet.connections[key]
        if not edge.is_terminal:
            # The command explicitly detaches this inlet; downstream equipment
            # stays in the design and validation reports the missing connection.
            endpoint=self.workspace.route(edge)[-1]
            edge=replace(edge,target_node=None,target_port=None,endpoint=endpoint)
        self.sheet.connections[key]=replace(edge,outlet=outlet)
        self.selected=key
        self._invalidate(); self._refresh()

    def make_context_menu(self,hit,point):
        """Build without posting, also allowing deterministic GUI acceptance tests."""
        menu=tk.Menu(self.root,tearoff=False)
        if self.busy: return menu
        if hit is None:
            membranes=tk.Menu(menu,tearoff=False)
            for name in self.presets.names:
                membranes.add_command(label=name,command=lambda p=name:self.add_equipment('membrane',point,p))
            menu.add_cascade(label='Add membrane',menu=membranes)
            for kind in ('compressor','splitter','mixer','feed'):
                menu.add_command(label='Add '+kind,command=lambda k=kind:self.add_equipment(k,point))
            menu.add_command(label='Add textbox',command=lambda:self.add_textbox(point))
            menu.add_separator()
            menu.add_command(label='Fit diagram',command=self.workspace.fit)
            return menu
        key=hit[1]
        self.select(key)
        if key == LEGEND_ID:
            menu.add_command(label='Reset legend position',command=self.reset_legend)
            return menu
        if key in self.sheet.annotations:
            menu.add_command(label='Edit textbox',command=lambda:self.edit_annotation(key))
            menu.add_command(label='Delete textbox',command=self.delete_selected)
            return menu
        if hit[0]=='port':
            source=(key,hit[2])
            edge=next((c for c in self.sheet.connections.values()
                       if (c.source_node,c.source_port)==source),None) if hit[3] else None
            if hit[3] and edge is None:
                for outlet,label in OUTLET_LABELS.items():
                    menu.add_command(label='Create '+label,command=lambda o=outlet:self.create_outlet(source,o))
                menu.add_separator()
                menu.add_command(label='Connect to equipment…',command=lambda:self._start_connection(source))
                return menu
            if edge: key=edge.id; self.select(key)
        if key in self.sheet.connections:
            edge=self.sheet.connections[key]
            choices=tk.Menu(menu,tearoff=False)
            for outlet,label in OUTLET_LABELS.items():
                choices.add_command(label=('✓ ' if edge.outlet==outlet else '')+label,
                                    command=lambda o=outlet:self.set_outlet(key,o))
            menu.add_cascade(label='Outlet' if edge.is_terminal else 'Disconnect downstream → outlet',menu=choices)
            menu.add_command(label='Add bend here',command=lambda:self.workspace.add_bend(key,point))
            if hit[0]=='bend':
                def remove_bend():
                    bends=list(self.workspace.route(self.sheet.connections[key])[1:-1])
                    del bends[hit[2]]
                    self.edit_geometry(key,waypoints=tuple(bends)); self._refresh()
                menu.add_command(label='Remove this bend',command=remove_bend)
            def reset_route():
                self.edit_geometry(key,waypoints=()); self._refresh()
            menu.add_command(label='Reset automatic route',command=reset_route)
            def reset_label():
                self.edit_geometry(key,label_dx=0,label_dy=0); self._refresh()
            menu.add_command(label='Reset label position',command=reset_label)
        else:
            def reset_box():
                self.edit_geometry(key,width=170,height=86,label_dx=0,label_dy=-32); self._refresh()
            menu.add_command(label='Reset box and label',command=reset_box)
        menu.add_separator()
        menu.add_command(label='Delete stream' if key in self.sheet.connections else 'Delete equipment',command=self.delete_selected)
        return menu

    def _start_connection(self,source):
        self.workspace.pending=source
        self.workspace.draw()
        self.set_status('Click an input port to connect. Esc cancels.')

    def context_menu(self,hit,point,event):
        if self.busy: return
        self.context_menus.dismiss()
        menu=self.make_context_menu(hit,point)
        self.context_menus.show(menu,event.x_root,event.y_root)

    def delete_selected(self):
        if self.busy or not self.selected or self.selected == LEGEND_ID: return
        annotation=self.selected in self.sheet.annotations
        self.sheet.delete(self.selected)
        self.selected=None
        if annotation: self.dirty=True
        else: self._invalidate()
        self._refresh()

    def _delete_key(self,event):
        if isinstance(event.widget,(tk.Entry,ttk.Entry,ttk.Combobox,tk.Text)): return
        self.delete_selected()

    def apply_selected(self,name,properties):
        if self.busy or not self.selected: return
        if self.selected in self.sheet.annotations:
            self.sheet.annotations[self.selected]=properties
            self.dirty=True
            self._refresh()
            return
        changed=True
        if self.selected in self.sheet.nodes:
            obj=self.sheet.nodes[self.selected]
            changed=name!=obj.name or properties!=obj.properties
            draft=deepcopy(self.sheet)
            if changed:
                draft.update_properties(self.selected,properties,self.compressor_map,name)
            geometry=self.editor.geometry()
            node=replace(draft.nodes[self.selected],**geometry)
            validate_properties(node)
            draft.nodes[self.selected]=node
            self.sheet=draft
        else:
            edge=properties if isinstance(properties,Connection) else replace(self.sheet.connections[self.selected],name=name)
            issue=self.sheet._connection_issue(edge)
            if issue: raise ValueError(issue.message)
            changed=edge!=self.sheet.connections[self.selected]
            self.sheet.connections[self.selected]=edge
        if changed: self._invalidate()
        else: self.dirty=True
        self._refresh()

    def _commit_editor(self):
        try:
            temperature=validate_temperature_c(float(self.temperature_var.get()))
        except ValueError as error:
            self.set_status(f'Invalid temperature: {error}')
            self.temperature_entry.focus_set()
            return False
        try:
            if self.editor.object:
                name,p=self.editor.collect()
                obj=self.selected_object()
                if obj and self.editor.has_changes():
                    self.apply_selected(name,p)
        except (ValueError,TypeError) as error:
            self.editor.error.set(str(error))
            self.set_status('Correct the selected object’s inputs before continuing.')
            return False
        if temperature != self.sheet.temperature_c:
            self.sheet.temperature_c=temperature
            self._invalidate()
            self._refresh()
        self.temperature_var.set(str(temperature).removesuffix('.0'))
        return True

    def apply_temperature(self):
        if self.busy: return
        if self._commit_editor():
            self.set_status(f'Assumed temperature set to {self.sheet.temperature_c:g} °C for all streams. '
                            'Run Simulation to update calculated results.')

    def compressor_preview(self,key,p):
        low,high=self.compressor_map.limits(p.rpm)
        source=self.sheet.direct_feed(key)
        feed=source.properties if source else None
        if feed is None and self.result and key in self.result.equipment:
            feed=self.result.equipment[key].inlets.get('inlet')
        if feed is None:
            return f'Curve: {low:,.0f}–{high:,.0f} slpm. Connect a feed or run upstream equipment to preview pressure. Downstream targets must match incoming flow.'
        inlet=feed.pressure_psia
        flow=feed.flow_slpm
        if p.mode=='pressure': flow=self.compressor_map.inverse(p.outlet_pressure_psia,p.rpm,inlet)
        elif p.mode=='flow': flow=p.flow_slpm
        point=self.compressor_map.forward(flow,p.rpm,inlet)
        linkage='Applying updates the connected feed flow.' if source else 'Requested point must match the incoming flow.'
        return (f'Curve: {low:,.0f}–{high:,.0f} slpm\n'
                f'{flow:,.2f} slpm → {point["p_out"]:.3f} psia ({psia_to_psig(point["p_out"]):.3f} psig)\n{linkage}')

    def save_preset(self,name,properties):
        preset_name=simpledialog.askstring('Save membrane preset','Preset name (O and N are reserved):',parent=self.root)
        if not preset_name: return
        preset_name=preset_name.strip()
        if preset_name in self.presets.names and preset_name not in ('O','N'):
            if not messagebox.askyesno('Replace preset',f'Replace {preset_name!r}? Existing nodes keep their properties.',parent=self.root): return
        self.presets.save(preset_name,properties)
        self.preset_combo.configure(values=self.presets.names)
        self.apply_selected(name,replace(properties,preset=preset_name))
        self.set_status(f'Saved reusable preset {preset_name!r}.')

    def show_issues(self,issues):
        self.workspace.error_nodes={issue.node_id for issue in issues if issue.node_id}
        self.workspace.error_connections={issue.connection_id for issue in issues if issue.connection_id}
        self.workspace.draw()
        self.results_panel.show_issues(issues)
        self.set_status(f'{len(issues)} issue(s). Select a message to highlight its equipment or stream.')

    def validate(self):
        if self.busy or not self._commit_editor(): return False
        issues=self.sheet.validate(self.compressor_map)
        if issues:
            self.show_issues(issues)
            return False
        self.workspace.error_nodes.clear(); self.workspace.error_connections.clear()
        self.workspace.draw()
        self.set_status('Connections and inputs are valid. Pressure compatibility and numerical results are checked during simulation.')
        return True

    def _set_busy(self,busy):
        self.busy=busy
        self.workspace.busy=busy
        self.editor.set_busy(busy)
        self.preset_combo.configure(state='disabled' if busy else 'readonly')
        self.temperature_entry.configure(state='disabled' if busy else 'normal')
        for button in self.buttons: button.state(['disabled'] if busy else ['!disabled'])
        self.run_button.configure(text='Running…' if busy else '▶ Run Simulation')

    def run_simulation(self):
        if self.busy or not self.validate(): return
        self.result=None
        self.results_panel.clear()
        self._refresh()
        self._set_busy(True)
        self.set_status('Calculating flowsheet…')
        snapshot=deepcopy(self.sheet)
        self.future=self.executor.submit(snapshot.simulate,self.compressor_map)
        self._poll_id=self.root.after(30,self._poll)

    def _poll(self):
        self._poll_id=None
        if self.closed: return
        if not self.future.done():
            self._poll_id=self.root.after(30,self._poll)
            return
        self._set_busy(False)
        try:
            self.result=self.future.result()
        except SimulationError as error:
            self.show_issues(error.issues)
            return
        except Exception as error:
            logging.exception('Unexpected simulation failure')
            self.show_issues([ValidationIssue(f'Unexpected simulation failure: {error}')])
            return
        self._refresh()
        self.results_panel.show_result(self.result)
        checked=[p for p in self.result.products.values() if p.passed is not None]
        self.set_status(f'Complete · {len(self.result.streams)} streams · '
                        f'{sum(p.passed for p in checked)}/{len(checked)} specified products pass · '
                        f'{len(self.result.warnings)} stage-cut warning(s).')

    def reset_results(self):
        if self.busy: return
        self.result=None
        self.results_panel.clear()
        self.workspace.error_nodes.clear(); self.workspace.error_connections.clear()
        self._refresh()
        self.set_status('Calculated results cleared; equipment and connections are unchanged.')

    def _confirm_discard(self):
        try:
            pending=float(self.temperature_var.get()) != self.sheet.temperature_c
        except ValueError:
            pending=True
        if self.editor.object:
            try:
                pending=pending or self.editor.has_changes()
            except (ValueError,TypeError): pending=True
        return not (self.dirty or pending) or messagebox.askyesno('Unsaved flowsheet',
            'Discard unsaved changes to this flowsheet?',parent=self.root)

    def _replace_sheet(self,sheet,path=None):
        self.context_menus.dismiss()
        self.sheet=sheet
        self.temperature_var.set(str(sheet.temperature_c).removesuffix('.0'))
        self.file_path=path
        self.selected=None
        self.result=None
        self.dirty=False
        self.results_panel.clear()
        self.workspace.error_nodes.clear(); self.workspace.error_connections.clear()
        self._refresh()
        self.workspace.fit()

    def clear(self):
        if self.busy or not self._confirm_discard(): return
        self._replace_sheet(Flowsheet())
        self.set_status('Empty flowsheet. Add a feed and equipment, then connect their ports.')

    def restore_default(self):
        if self.busy or not self._confirm_discard(): return
        self._replace_sheet(default_flowsheet(self.presets))
        self.set_status('Default OOON design restored. Run Simulation to calculate results.')

    def save(self,save_as=False):
        if self.busy or not self._commit_editor(): return
        path=self.file_path
        if path is None or save_as:
            selected=filedialog.asksaveasfilename(parent=self.root,title='Save flowsheet',defaultextension='.json',
                initialdir=str(PROJECT_ROOT/'mercury'),filetypes=[('Mercury flowsheet','*.json')])
            if not selected: return
            path=Path(selected)
        try:
            self.sheet.save(path)
            self.file_path=path
            self.dirty=False
            self._title()
            self.set_status(f'Saved inputs and layout to {path.name}. Calculated results are not saved.')
        except (ValueError,OSError) as error:
            messagebox.showerror('Could not save flowsheet',str(error),parent=self.root)

    def load_path(self,path):
        # Parse and structurally validate completely before replacing the current state.
        sheet=Flowsheet.load(Path(path))
        self._replace_sheet(sheet,Path(path))
        issues=self.sheet.validate(self.compressor_map)
        if issues: self.show_issues(issues)
        else: self.set_status(f'Loaded {Path(path).name}. Run Simulation to calculate results.')

    def load(self):
        if self.busy or not self._confirm_discard(): return
        path=filedialog.askopenfilename(parent=self.root,title='Open flowsheet',initialdir=str(PROJECT_ROOT/'mercury'),
                                       filetypes=[('Mercury flowsheet','*.json')])
        if not path: return
        try: self.load_path(path)
        except (ValueError,OSError) as error: messagebox.showerror('Could not load flowsheet',str(error),parent=self.root)

    def close(self):
        if not self._confirm_discard(): return
        self.closed=True
        self.context_menus.close()
        if self._layout_job: self.root.after_cancel(self._layout_job)
        if self._poll_id: self.root.after_cancel(self._poll_id)
        self.executor.shutdown(wait=False,cancel_futures=True)
        self.root.destroy()


def launch():
    try:
        root=tk.Tk()
    except tk.TclError as error:
        raise RuntimeError(f'Cannot initialize Tk/display: {error}. Run Mercury from a graphical desktop session.') from error
    try:
        MainWindow(root)
    except Exception:
        root.destroy()
        raise
    root.mainloop()
