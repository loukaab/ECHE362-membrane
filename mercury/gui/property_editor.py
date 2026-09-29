"""Editable inputs and read-only calculated conditions for the selection."""

import tkinter as tk
from tkinter import ttk, colorchooser
from dataclasses import replace

from mercury.simulation.models import (
    Equipment, Connection, FeedProperties, MembraneProperties, CompressorProperties,
    SplitterProperties, ProductProperties, EmptyProperties, SimulationResult,
    TextAnnotation, LegendPosition,
)
from mercury.simulation.units import psia_to_psig


def stream_text(stream) -> str:
    return (f"Temperature {stream.temperature_c:.0f} °C (assumed)\n"
            f"Flow       {stream.flow_slpm:,.3f} slpm\n"
            f"O2 fraction {stream.oxygen:.6f} mol/mol\n"
            f"O2          {100*stream.oxygen:.4f} mol%\n"
            f"N2          {100*(1-stream.oxygen):.4f} mol%\n"
            f"Pressure   {stream.pressure_psia:.4f} psia\n"
            f"            {psia_to_psig(stream.pressure_psia):.4f} psig")


class PropertyEditor(ttk.Frame):
    def __init__(self, parent, presets, compressor_map, on_apply, on_save_preset, on_preview):
        super().__init__(parent, padding=14)
        self.presets, self.compressor_map = presets, compressor_map
        self.on_apply, self.on_save_preset, self.on_preview = on_apply, on_save_preset, on_preview
        self.object = None
        self.variables = {}
        self.entries = {}
        self.busy = False
        self.heading = ttk.Label(self, text="Properties", style="Section.TLabel")
        self.heading.pack(anchor="w", pady=(0,6))
        self.help = ttk.Label(self, text="Select equipment or a stream.", wraplength=300, foreground="#66768a")
        self.help.pack(anchor="w", pady=(0,12))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self.input_page = ttk.Frame(self.tabs)
        self.results_page = ttk.Frame(self.tabs, padding=4)
        self.tabs.add(self.input_page, text="Inputs")
        self.tabs.add(self.results_page, text="Results")
        input_canvas = tk.Canvas(self.input_page, background="#ffffff", highlightthickness=0,
                                 width=310, height=300)
        input_scroll = ttk.Scrollbar(self.input_page, orient="vertical", command=input_canvas.yview)
        input_canvas.configure(yscrollcommand=input_scroll.set)
        input_scroll.pack(side="right", fill="y")
        input_canvas.pack(side="left", fill="both", expand=True)
        self.input_content = ttk.Frame(input_canvas, padding=(0, 6, 5, 4))
        input_window = input_canvas.create_window(0, 0, anchor="nw", window=self.input_content)
        self.input_content.bind("<Configure>", lambda event: input_canvas.configure(scrollregion=input_canvas.bbox("all")))
        input_canvas.bind("<Configure>", lambda event: input_canvas.itemconfigure(input_window, width=event.width))
        self.form = ttk.Frame(self.input_content)
        self.form.pack(fill="x")
        self.form.columnconfigure(1, weight=1)
        self.buttons = ttk.Frame(self.input_content)
        self.buttons.pack(fill="x", pady=10)
        self.apply_button = ttk.Button(self.buttons, text="Apply changes", command=self.apply)
        self.apply_button.pack(side="left")
        self.preset_button = ttk.Button(self.buttons, text="Save as preset", command=self.save_preset)
        self.preview = ttk.Label(self.input_content, text="", wraplength=280, foreground="#395778")
        self.preview.pack(fill="x", pady=(2,6))
        self.error = tk.StringVar()
        ttk.Label(self.input_content, textvariable=self.error, foreground="#b43142", wraplength=280).pack(fill="x")
        self.result_text = tk.Text(self.results_page, height=13, width=32, wrap="word", relief="flat",
                                   background="#f6f8fb", foreground="#263c55", padx=8, pady=8,
                                   font=("TkFixedFont",9), state="disabled")
        result_scroll = ttk.Scrollbar(self.results_page, orient="vertical", command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=result_scroll.set)
        result_scroll.pack(side="right", fill="y")
        self.result_text.pack(side="left", fill="both", expand=True)
        self._row = 0

    def _field(self, name: str, label: str, value, choices=None):
        variable = tk.StringVar(value=str(value))
        ttk.Label(self.form, text=label).grid(row=self._row, column=0, sticky="w", padx=(0,8), pady=5)
        if choices is not None:
            entry = ttk.Combobox(self.form, textvariable=variable, values=choices, state="readonly", width=18)
        else:
            entry = ttk.Entry(self.form, textvariable=variable, width=20)
        entry.grid(row=self._row, column=1, sticky="ew", pady=5)
        self._row += 1
        self.variables[name], self.entries[name] = variable, entry
        return variable

    def _pressure(self, name: str, label: str, value):
        variable = self._field(name,label,value)
        gauge = ttk.Label(self.form, foreground="#6b7b8d")
        gauge.grid(row=self._row,column=1,sticky="w")
        self._row += 1
        def update(*args):
            try: gauge.configure(text=f"{psia_to_psig(float(variable.get())):.3f} psig")
            except (ValueError,TypeError): gauge.configure(text="Enter an absolute pressure")
        variable.trace_add("write",update)
        update()

    def show(self, obj: Equipment | Connection | TextAnnotation | LegendPosition | None, result: SimulationResult | None) -> None:
        self.object = obj
        for child in self.form.winfo_children(): child.destroy()
        self.variables, self.entries, self._row = {}, {}, 0
        self.error.set("")
        self.preview.configure(text="")
        self.preset_button.pack_forget()
        if obj is None:
            self.heading.configure(text="Properties")
            self.help.configure(text="Select a block to edit its inputs, or a connection to inspect its stream.")
            self.apply_button.state(["disabled"])
            self._set_results("Run Simulation to calculate stream conditions.")
            return
        if isinstance(obj,LegendPosition):
            self.heading.configure(text="Stream legend")
            self.help.configure(text="Drag the legend on the diagram. Right-click it to reset its position.")
            self.preview.configure(text="Shapes identify temperature, pressure, flow, and O₂ fraction. Units and the temperature assumption appear here once for all streams.")
            self._set_results("The legend is part of the diagram layout and does not change simulation results.")
            self.tabs.select(self.input_page)
            self.set_busy(self.busy)
            return
        if isinstance(obj,TextAnnotation):
            self.heading.configure(text=f"Textbox · {obj.id}")
            self.help.configure(text="Edit your note and formatting, then Apply changes. Drag its text or resize its width on the diagram.")
            text=tk.Text(self.form,height=6,width=20,wrap="word",undo=True,font=("TkDefaultFont",11))
            text.insert("1.0",obj.text)
            text.grid(row=self._row,column=0,columnspan=2,sticky="ew",pady=(0,8))
            self.entries["text"]=text
            self._row+=1
            self._field("width","Width",obj.width)
            self._field("font_size","Font size",obj.font_size)
            self.variables["bold"]=tk.BooleanVar(value=obj.bold)
            bold=ttk.Checkbutton(self.form,text="Bold",variable=self.variables["bold"])
            bold.grid(row=self._row,column=1,sticky="w",pady=5)
            self.entries["bold"]=bold
            self._row+=1
            self._field("color","Text color",obj.color)
            color=ttk.Button(self.form,text="Choose color…",command=self._choose_color)
            color.grid(row=self._row,column=1,sticky="ew",pady=5)
            self.entries["color_picker"]=color
            self._row+=1
            self.preview.configure(text="Width controls wrapping; height follows the text. Formatting and layout edits keep calculated results.")
            self._set_results("Textboxes are diagram annotations and do not take part in simulation.")
            self.tabs.select(self.input_page)
            self.set_busy(self.busy)
            return
        self.heading.configure(text=obj.name)
        self._field("name","Name",obj.name)
        if isinstance(obj, Connection):
            destination = obj.outlet if obj.is_terminal else f"{obj.target_node} / {obj.target_port}"
            self.help.configure(text=f"{obj.source_node} / {obj.source_port}\n→ {destination}\nDrag the label, route handles, or terminal endpoint on the diagram.")
            if obj.is_terminal:
                self._field("outlet","Outlet role",obj.outlet,["oxygen","nitrogen","report","vent"])
                self.preview.configure(text="oxygen / nitrogen: collect and check product specifications. report: collect without specifications. vent: purge/discard. All remain in material balances.")
            text = stream_text(result.streams[obj.id]) if result and obj.id in result.streams else "Stream conditions are calculated from upstream equipment."
            self._set_results(text)
            self.tabs.select(self.results_page if result else self.input_page)
        else:
            self._field("width","Box width",obj.width)
            self._field("height","Box height",obj.height)
            p = obj.properties
            self.help.configure(text=f"{obj.id} · {obj.kind.title()}\nEdit inputs, then Apply changes.")
            if isinstance(p, FeedProperties):
                self._field("flow_slpm","Flow (slpm)",p.flow_slpm)
                self._field("oxygen_percent","O2 (mol%)",100*p.oxygen)
                self._pressure("pressure_psia","Pressure (psia)",p.pressure_psia)
                self.preview.configure(text="N2 is the remaining mole fraction. A directly connected compressor shares this feed flow.")
            elif isinstance(p, MembraneProperties):
                self._field("preset","Starting preset",p.preset,self.presets.names)
                self.entries["preset"].bind("<<ComboboxSelected>>",self._choose_preset)
                self._field("area_m2","Area (m²)",p.area_m2)
                self._field("alpha","O2/N2 selectivity",p.alpha)
                self._field("oxygen_permeance","O2 permeance¹",p.oxygen_permeance)
                self._pressure("permeate_pressure_psia","Permeate (psia)",p.permeate_pressure_psia)
                self.preview.configure(text="¹ slpm/(psi·m²). Editing these values changes only this node. Stage cut is calculated.")
                self.preset_button.pack(side="left",padx=(8,0))
            elif isinstance(p, CompressorProperties):
                self._field("rpm","Speed (rpm)",p.rpm,[str(r) for r in self.compressor_map.speeds])
                self._field("mode","Control",p.mode,["inlet","flow","pressure"])
                self._field("flow_slpm","Requested slpm",p.flow_slpm if p.flow_slpm is not None else 10000)
                self._pressure("outlet_pressure_psia","Requested psia",p.outlet_pressure_psia if p.outlet_pressure_psia is not None else 123.627)
                self.help.configure(text=f"{obj.id} · Compressor\nInlet: follow upstream flow. Flow/pressure: request an operating point.")
                for name in ["rpm","mode","flow_slpm","outlet_pressure_psia"]:
                    self.variables[name].trace_add("write",self._compressor_preview)
                self._compressor_preview()
            elif isinstance(p, SplitterProperties):
                self._field("fraction","Fraction to A",p.fraction)
                self.preview.configure(text="A receives this fraction of inlet flow; B receives the remainder. Composition and pressure are unchanged.")
            elif isinstance(p, ProductProperties):
                self._field("role","Target",p.role,["oxygen","nitrogen","report"])
                self.preview.configure(text="Oxygen: ≥3,400 slpm and ≥40% O2.\nNitrogen: ≥6,000 slpm and ≤5% O2.\nReport: no specification checks.")
            elif obj.kind == "mixer":
                self.preview.configure(text="Connect both inlet ports. Mixing is flow-weighted and requires equal inlet pressures.")
            else:
                self.preview.configure(text="An intentional external outlet, included in all material balances.")
            self._show_equipment_result(obj,result)
        self.set_busy(self.busy)

    def _choose_preset(self, event=None):
        p = self.presets.get(self.variables["preset"].get())
        for name in ("area_m2","alpha","oxygen_permeance","permeate_pressure_psia"):
            self.variables[name].set(str(getattr(p,name)))

    def collect(self):
        values = {key: var.get() for key,var in self.variables.items()}
        if isinstance(self.object,LegendPosition): return "Legend",self.object
        if isinstance(self.object,TextAnnotation):
            annotation=replace(self.object,text=self.entries['text'].get('1.0','end-1c'),
                               width=float(values['width']),font_size=int(values['font_size']),
                               bold=values['bold'],color=values['color'])
            return annotation.id,annotation
        name = values.pop("name").strip()
        if not name: raise ValueError("Name cannot be empty")
        if isinstance(self.object,Connection):
            return name,replace(self.object,name=name,outlet=values.get('outlet',self.object.outlet))
        kind = self.object.kind
        if kind == "feed":
            p = FeedProperties(float(values['flow_slpm']),float(values['oxygen_percent'])/100,float(values['pressure_psia']))
        elif kind == "membrane":
            p = MembraneProperties(float(values['area_m2']),float(values['alpha']),float(values['oxygen_permeance']),
                                   values['preset'],float(values['permeate_pressure_psia']))
        elif kind == "compressor":
            mode = values['mode']
            p = CompressorProperties(int(values['rpm']),mode,float(values['flow_slpm']) if mode=='flow' else None,
                                     float(values['outlet_pressure_psia']) if mode=='pressure' else None)
        elif kind == "splitter": p = SplitterProperties(float(values['fraction']))
        elif kind == "product": p = ProductProperties(values['role'])
        else: p = EmptyProperties()
        return name,p

    def geometry(self):
        if not isinstance(self.object,Equipment): return {}
        return {key:float(self.variables[key].get()) for key in ('width','height')}

    def has_changes(self):
        if isinstance(self.object,LegendPosition): return False
        name,p=self.collect()
        obj=self.object
        if isinstance(obj,(Connection,TextAnnotation)): return p!=obj
        return name!=obj.name or p!=obj.properties or any(getattr(obj,key)!=value for key,value in self.geometry().items())

    def focus_text(self):
        if isinstance(self.object,TextAnnotation) and not self.busy:
            self.tabs.select(self.input_page)
            self.entries['text'].focus_set()

    def _choose_color(self):
        if self.busy: return
        initial=self.variables['color'].get()
        try: self.winfo_rgb(initial)
        except tk.TclError: initial='#243b55'
        _,color=colorchooser.askcolor(initialcolor=initial,parent=self.winfo_toplevel(),title="Textbox color")
        if color: self.variables['color'].set(color)

    def apply(self) -> None:
        if self.busy or self.object is None: return
        try:
            name,parameters = self.collect()
            self.on_apply(name,parameters)
        except (ValueError,TypeError) as error:
            self.error.set(str(error))

    def save_preset(self) -> None:
        if self.busy: return
        try:
            name,parameters = self.collect()
            self.on_save_preset(name,parameters)
        except (ValueError,TypeError,OSError) as error:
            self.error.set(str(error))

    def _compressor_preview(self,*args):
        if self.object is None or self.object.kind != 'compressor': return
        mode = self.variables['mode'].get()
        for key,enabled in [('flow_slpm',mode=='flow'),('outlet_pressure_psia',mode=='pressure')]:
            self.entries[key].configure(state='normal' if enabled and not self.busy else 'disabled')
        try:
            _,p = self.collect()
            self.preview.configure(text=self.on_preview(self.object.id,p))
        except (ValueError,TypeError) as error:
            self.preview.configure(text=str(error))

    def _show_equipment_result(self,node,result):
        if not result or node.id not in result.equipment:
            self._set_results("No current results. Apply changes and run the simulation.")
            return
        eq = result.equipment[node.id]
        sections = []
        if eq.stage_cut is not None:
            status = "within 0.20–0.40" if .2 <= eq.stage_cut <= .4 else "outside recommended range"
            sections.append(f"Stage cut: {eq.stage_cut:.5f}\n{status}")
        if eq.ideal_power_kw is not None:
            sections.append(f"Ideal power: {eq.ideal_power_kw:.3f} kW\n(Not motor power)")
        for label,streams in [('Inlet',eq.inlets),('Outlet',eq.outlets)]:
            for port,stream in streams.items():
                sections.append(f"{label}: {port}\n{stream_text(stream)}")
        self._set_results("\n\n".join(sections))

    def _set_results(self,text):
        self.result_text.configure(state='normal')
        self.result_text.delete('1.0','end')
        self.result_text.insert('1.0',text)
        self.result_text.configure(state='disabled')

    def set_busy(self,busy):
        self.busy = busy
        for entry in self.entries.values():
            entry.configure(state='disabled' if busy else 'readonly' if isinstance(entry,ttk.Combobox) else 'normal')
        self.apply_button.state(['disabled'] if busy or self.object is None or isinstance(self.object,LegendPosition) else ['!disabled'])
        self.preset_button.state(['disabled'] if busy else ['!disabled'])
        if isinstance(self.object,Equipment) and self.object.kind=='compressor': self._compressor_preview()
