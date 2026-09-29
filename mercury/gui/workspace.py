"""Diagram rendering and direct manipulation; all saved geometry lives in the model."""

import math
import tkinter as tk
from tkinter import ttk

from mercury.simulation.flowsheet import Flowsheet
from mercury.simulation.models import LEGEND_ID
from .diagram_labels import DiagramLabels, stream_values


OUTLET_LABELS = {"oxygen": "O₂ product", "nitrogen": "N₂ product",
                 "report": "Collected · report only", "vent": "Purge / vent"}


class Workspace(ttk.Frame):
    WIDTH, HEIGHT = 170, 86
    PORT_RADIUS, PORT_HIT_RADIUS = 9, 16  # Screen pixels, independent of zoom.

    def __init__(self, parent, on_select, on_connect, on_move, on_status):
        super().__init__(parent)
        self.on_select, self.on_connect = on_select, on_connect
        self.on_move, self.on_status = on_move, on_status
        self.on_geometry = lambda key, **changes: None
        self.on_context = lambda hit, point, event: None
        self.on_edit_annotation = lambda key: None
        self.sheet = Flowsheet()
        self.result = None
        self.selected = None
        self.error_nodes, self.error_connections = set(), set()
        self.label_fields = {"temperature", "pressure", "flow", "oxygen"}
        self.busy = False
        self.scale = .75
        self.pending = None
        self.drag = None
        self.items = {}
        self.canvas = tk.Canvas(self, background="#f5f7fa", highlightthickness=0)
        self.labels = DiagramLabels(self.canvas, self.items)
        xbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        ybar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        for event, callback in [("<Button-1>", self._press), ("<B1-Motion>", self._drag),
                                ("<ButtonRelease-1>", self._release), ("<Motion>", self._motion),
                                ("<Button-3>", self._context), ("<Double-Button-1>", self._double_click)]:
            self.canvas.bind(event, callback)
        self.canvas.bind("<Escape>", lambda event: self.cancel_connection())
        self.canvas.bind("<ButtonPress-2>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B2-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<Control-MouseWheel>", lambda e: self.zoom(1.15 if e.delta > 0 else 1/1.15))
        self.canvas.bind("<Control-Button-4>", lambda e: self.zoom(1.15))
        self.canvas.bind("<Control-Button-5>", lambda e: self.zoom(1/1.15))

    def set_model(self, sheet, result=None):
        self.sheet, self.result = sheet, result
        self.pending = None
        self.draw()

    def world_point(self, event):
        return self.canvas.canvasx(event.x) / self.scale, self.canvas.canvasy(event.y) / self.scale

    def port_position(self, node, port, output):
        x, y, w, h = node.x, node.y, node.width, node.height
        if node.kind == "membrane" and output:
            point = (x+w/2, y+h) if port == "permeate" else (x+w, y+h/2)
        else:
            ports = node.outlet_ports if output else node.inlet_ports
            point = x+w if output else x, y + (ports.index(port)+1) / (len(ports)+1) * h
        return point[0]*self.scale, point[1]*self.scale

    def route(self, edge):
        """World coordinates: attached ends follow equipment; bends remain editable."""
        source = self.sheet.nodes[edge.source_node]
        sx, sy = (v/self.scale for v in self.port_position(source, edge.source_port, True))
        down = source.kind == "membrane" and edge.source_port == "permeate"
        if edge.is_terminal:
            tx, ty = edge.endpoint or ((sx, sy+190) if down else (sx+180, sy))
        else:
            target = self.sheet.nodes[edge.target_node]
            tx, ty = (v/self.scale for v in self.port_position(target, edge.target_port, False))
        if edge.waypoints:
            return [(sx, sy), *edge.waypoints, (tx, ty)]
        if down:
            points = [(sx, sy), (sx, (sy+ty)/2), (tx, (sy+ty)/2), (tx, ty)]
        else:
            mid = (sx+tx)/2 if tx > sx else sx+45
            points = [(sx, sy), (mid, sy), (mid, ty), (tx, ty)]
        # Drop coincident vertices, leaving simple straight streams easy to edit.
        return [p for i, p in enumerate(points) if not i or p != points[i-1]]

    def stream_label(self, edge):
        return "\n".join([value for _,value in stream_values(edge,self.result,self.label_fields)] + [edge.name])

    def _label(self, x, y, text, hit, width=210, color="#243b55", anchor="center"):
        c = self.canvas
        label = c.create_text(x, y, text=text, anchor=anchor, justify="left",
                              font=("TkDefaultFont", max(8, round(10*self.scale))),
                              fill=color, width=max(100, width*self.scale))
        box = c.bbox(label)
        background = c.create_rectangle(box[0]-4, box[1]-3, box[2]+4, box[3]+3,
                                        fill="#f5f7fa", outline="")
        c.tag_lower(background, label)
        self.items[label] = self.items[background] = hit

    def draw(self):
        c, scale = self.canvas, self.scale
        c.delete("all")
        self.items.clear()
        self.labels.badge_geometry.clear()
        for edge in self.sheet.connections.values():
            if edge.source_node not in self.sheet.nodes or (not edge.is_terminal and edge.target_node not in self.sheet.nodes):
                continue
            points = self.route(edge)
            color = ("#bc3846" if edge.id in self.error_connections else "#2363b5"
                     if edge.id == self.selected else "#39836c" if edge.is_terminal and edge.outlet != "vent" else "#78879a")
            line = c.create_line(*(v*scale for p in points for v in p), fill=color,
                                  width=3 if edge.id == self.selected else 2,
                                  arrow="last", arrowshape=(12, 14, 5), joinstyle="round")
            self.items[line] = ("edge", edge.id)
            sx, sy = points[0]; tx, ty = points[-1]
            if edge.is_terminal:
                if abs(tx-sx) < abs(ty-sy):
                    lx, ly, anchor = tx+22, (sy+ty)/2, "w"
                else:
                    lx, ly, anchor = (sx+tx)/2, ty-22, "s"
            else:
                lx, ly, anchor = (sx+tx)/2, min(sy, ty)-160, "s"
            self.labels.stream((lx+edge.label_dx)*scale, (ly+edge.label_dy)*scale,
                               edge,self.result,self.label_fields,color,anchor,scale)
            if edge.id == self.selected:
                for i, (x, y) in enumerate(points[1:-1]):
                    h = c.create_rectangle(x*scale-5, y*scale-5, x*scale+5, y*scale+5,
                                            fill="#fff", outline="#2363b5", width=2)
                    self.items[h] = ("bend", edge.id, i)
                for i, (a, b) in enumerate(zip(points, points[1:])):
                    x, y = (a[0]+b[0])/2*scale, (a[1]+b[1])/2*scale
                    h = c.create_oval(x-4, y-4, x+4, y+4, fill="#cfe2ff", outline="#2363b5")
                    self.items[h] = ("segment", edge.id, i)
                if edge.is_terminal:
                    h = c.create_oval(tx*scale-7, ty*scale-7, tx*scale+7, ty*scale+7,
                                      fill="#fff", outline=color, width=2)
                    self.items[h] = ("endpoint", edge.id)

        colors = {"feed": "#e6f2ed", "membrane": "#eaf1fc", "compressor": "#fff1d9",
                  "splitter": "#efeafa", "mixer": "#efeafa"}
        for node in self.sheet.nodes.values():
            x, y, w, h = node.x*scale, node.y*scale, node.width*scale, node.height*scale
            outline = "#bc3846" if node.id in self.error_nodes else "#2363b5" if node.id == self.selected else "#9aa9ba"
            fill = colors.get(node.kind, "#e9edf2")
            eq = self.result.equipment.get(node.id) if self.result else None
            detail = node.kind.title()
            if node.kind == "membrane": detail += f" · {node.properties.preset}"
            elif node.kind == "compressor": detail += f" · {node.properties.rpm:,} rpm"
            elif node.kind == "splitter": detail += f" · A {node.properties.fraction:.0%}"
            if eq and eq.stage_cut is not None:
                detail += f"\nStage cut {eq.stage_cut:.3f}"
                if not .2 <= eq.stage_cut <= .4:
                    fill = "#fff0d6"
                    detail += " · warning"
            if node.kind in ("vent", "product"):
                # Unconnected legacy placeholders retain their identity for repair.
                box = c.create_line(x, y+h/2, x+w, y+h/2, arrow="last", width=2, fill=outline)
                detail = "Unconnected legacy outlet"
            else:
                box = c.create_rectangle(x, y, x+w, y+h, fill=fill, outline=outline,
                                         width=3 if node.id == self.selected or node.id in self.error_nodes else 1.5)
                # Fixed short symbols always fit; full names live outside the box.
                symbol = {"membrane": "MEM", "compressor": "COMP", "feed": "FEED",
                          "splitter": "SPLIT", "mixer": "MIX"}[node.kind]
                label = c.create_text(x+w/2, y+h/2, text=symbol, fill="#334c69",
                                      font=("TkDefaultFont", max(7, round(13*scale)), "bold"))
                self.items[label] = ("node", node.id)
            self.items[box] = ("node", node.id)
            self._label((node.x+node.width/2+node.label_dx)*scale, (node.y+node.label_dy)*scale,
                        node.name+"\n"+detail, ("node_label", node.id), anchor="s")
            if node.id == self.selected and node.kind not in ("product", "vent"):
                handle = c.create_rectangle(x+w-8, y+h-8, x+w+3, y+h+3,
                                             fill="#2363b5", outline="#fff")
                self.items[handle] = ("resize", node.id)
            for output, ports in [(False, node.inlet_ports), (True, node.outlet_ports)]:
                for port in ports:
                    px, py = self.port_position(node, port, output)
                    r = self.PORT_RADIUS
                    dot = c.create_oval(px-r, py-r, px+r, py+r,
                                         fill="#e49c32" if self.pending == (node.id, port) else "#356faa" if output else "#fff",
                                         outline="#356faa", width=2)
                    self.items[dot] = ("port", node.id, port, output)
                    if port in ("a", "b", "permeate", "retentate"):
                        label = c.create_text(px + (-20 if output else 20), py,
                                              text={"permeate": "P", "retentate": "R"}.get(port, port.upper()),
                                              font=("TkDefaultFont", 8), fill="#4d6078")
                        self.items[label] = ("port", node.id, port, output)
        self.labels.legend(self.sheet.legend, scale, self.selected == LEGEND_ID)
        for annotation in self.sheet.annotations.values():
            x,y,w = annotation.x*scale,annotation.y*scale,annotation.width*scale
            text = c.create_text(x,y,anchor="nw",text=annotation.text if annotation.text.strip() else "[Text]",width=w,
                                 font=self.labels.font(scale,annotation.font_size,annotation.bold),
                                 fill=annotation.color if annotation.text.strip() else "#78879a")
            self.items[text] = ("annotation",annotation.id)
            if annotation.id == self.selected:
                height = max(self.labels.font(scale,annotation.font_size).metrics("linespace"),c.bbox(text)[3]-y)
                box = c.create_rectangle(x-3,y-3,x+w+3,y+height+3,outline="#2363b5",dash=(3,3))
                self.items[box] = ("annotation",annotation.id)
                c.tag_lower(box,text)
                handle = c.create_rectangle(x+w-5,y+height-5,x+w+5,y+height+5,fill="#2363b5",outline="#fff")
                self.items[handle] = ("annotation_resize",annotation.id)
        bounds = c.bbox("all")
        if bounds:
            c.configure(scrollregion=(min(0, bounds[0]-40), min(0, bounds[1]-40),
                                      max(c.winfo_width(), bounds[2]+50), max(c.winfo_height(), bounds[3]+50)))

    def _hit(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        overlapping = self.canvas.find_overlapping(x-3, y-3, x+3, y+3)
        for item in reversed(overlapping):
            hit = self.items.get(item)
            if hit and hit[0] in ("resize", "endpoint", "bend", "segment", "annotation_resize", "annotation", "legend"):
                return hit
        # Give forgiving circular port targets priority over neighboring labels.
        nearest = None
        distance = self.PORT_HIT_RADIUS
        for node in self.sheet.nodes.values():
            for output, ports in [(False, node.inlet_ports), (True, node.outlet_ports)]:
                for port in ports:
                    px, py = self.port_position(node, port, output)
                    d = math.hypot(px-x, py-y)
                    if d < distance:
                        nearest, distance = ("port", node.id, port, output), d
        if nearest: return nearest
        for item in reversed(overlapping):
            if item in self.items: return self.items[item]
        return None

    def _press(self, event):
        self.canvas.focus_set()
        if self.busy: return
        hit = self._hit(event)
        self.drag = None
        if hit and hit[0] == "port":
            _, key, port, output = hit
            if output:
                existing = next((c for c in self.sheet.connections.values()
                                 if (c.source_node, c.source_port) == (key, port)), None)
                if existing:
                    self.on_select(existing.id)
                    self.on_status("This outlet is connected. Right-click its stream to change the outlet or delete it.")
                    return
                self.pending = (key, port)
                self.on_status(f"Connect {key} {port}: click an input. Right-click the outlet for a product or purge. Esc cancels.")
                self.draw()
            elif self.pending:
                origin, self.pending = self.pending, None
                self.on_connect(origin, (key, port))
            else:
                self.on_status("Start at a filled output port, then click an open input port.")
            return
        self.cancel_connection()
        self.on_select(hit[1] if hit else None)
        if hit:
            key = hit[1]
            obj = (self.sheet.legend if key == LEGEND_ID else self.sheet.nodes.get(key)
                   or self.sheet.connections.get(key) or self.sheet.annotations.get(key))
            self.drag = (hit, self.world_point(event), obj, self.route(obj) if key in self.sheet.connections else None)
            # Equipment is mutable: capture geometry before moving it.
            if key in self.sheet.nodes:
                from copy import copy
                self.drag = (hit, self.world_point(event), copy(obj), None)

    @staticmethod
    def _snap(value):
        return round(value/10)*10

    def _drag(self, event):
        if self.busy or not self.drag: return
        hit, start, obj, points = self.drag
        key, kind = hit[1], hit[0]
        end = self.world_point(event)
        dx, dy = end[0]-start[0], end[1]-start[1]
        if kind == "node":
            self.on_move(key, max(20, obj.x+dx), max(20, obj.y+dy))
        elif kind in ("annotation", "legend"):
            self.on_geometry(key,x=obj.x+dx,y=obj.y+dy)
        elif kind == "annotation_resize":
            self.on_geometry(key,width=max(40,self._snap(obj.width+dx)))
        elif kind in ("node_label", "stream_label"):
            self.on_geometry(key, label_dx=obj.label_dx+dx, label_dy=obj.label_dy+dy)
        elif kind == "resize":
            self.on_geometry(key, width=max(100, self._snap(obj.width+dx)), height=max(64, self._snap(obj.height+dy)))
        elif kind == "endpoint":
            self.on_geometry(key, endpoint=(self._snap(points[-1][0]+dx), self._snap(points[-1][1]+dy)))
        elif kind == "bend":
            bends = list(points[1:-1])
            i = hit[2]
            bends[i] = self._snap(bends[i][0]+dx), self._snap(bends[i][1]+dy)
            self.on_geometry(key, waypoints=tuple(bends))
        elif kind == "segment":
            # Move a whole segment perpendicular to itself, keeping orthogonal
            # routes orthogonal. Endpoint stubs keep equipment ports attached.
            i = hit[2]
            a, b = points[i], points[i+1]
            if abs(a[1]-b[1]) < abs(a[0]-b[0]):
                a2, b2 = (a[0], self._snap(a[1]+dy)), (b[0], self._snap(b[1]+dy))
            else:
                a2, b2 = (self._snap(a[0]+dx), a[1]), (self._snap(b[0]+dx), b[1])
            bends = points[1:i] + [a2, b2] + points[i+2:-1]
            self.on_geometry(key, waypoints=tuple(bends))
        self.draw()

    def _release(self, event):
        moved = self.drag is not None
        self.drag = None
        if moved and not self.busy: self.on_select(self.selected)

    def _motion(self, event):
        self.canvas.delete("preview")
        if self.pending and not self.busy:
            node, port = self.pending
            x, y = self.port_position(self.sheet.nodes[node], port, True)
            tx, ty = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
            hit = self._hit(event)
            if hit and hit[0] == "port" and not hit[3]:
                tx, ty = self.port_position(self.sheet.nodes[hit[1]], hit[2], False)
                self.canvas.create_oval(tx-14, ty-14, tx+14, ty+14, outline="#39836c", width=2, tags="preview")
            self.canvas.create_line(x, y, tx, ty, dash=(4, 3), fill="#d29228", tags="preview")

    def _context(self, event):
        self.cancel_connection()
        if not self.busy: self.on_context(self._hit(event), self.world_point(event), event)

    def add_bend(self, key, point):
        if self.busy: return
        edge = self.sheet.connections[key]
        points = self.route(edge)
        def distance(i):
            a, b = points[i], points[i+1]
            vx, vy = b[0]-a[0], b[1]-a[1]
            t = max(0, min(1, ((point[0]-a[0])*vx+(point[1]-a[1])*vy)/(vx*vx+vy*vy or 1)))
            return math.hypot(point[0]-a[0]-t*vx, point[1]-a[1]-t*vy)
        i = min(range(len(points)-1), key=distance)
        points.insert(i+1, tuple(self._snap(v) for v in point))
        self.on_geometry(key, waypoints=tuple(points[1:-1]))
        self.on_select(key)

    def _double_click(self, event):
        if self.busy: return
        hit = self._hit(event)
        if hit and hit[0] == "annotation":
            self.drag = None
            self.on_edit_annotation(hit[1])
            return
        if hit and hit[0] in ("edge", "segment", "bend"):
            self.drag = None
            self.add_bend(hit[1], self.world_point(event))

    def cancel_connection(self):
        self.pending = None
        self.canvas.delete("preview")

    def fit(self):
        self.update_idletasks()
        self.draw()
        bounds = self.canvas.bbox("all")
        if not bounds: return
        width, height = (bounds[2]-min(0, bounds[0])+70)/self.scale, (bounds[3]-min(0, bounds[1])+70)/self.scale
        self.scale = max(.3, min(1.1, (self.canvas.winfo_width()-20)/width, (self.canvas.winfo_height()-20)/height))
        self.draw()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def zoom(self, factor):
        self.scale = min(1.8, max(.3, self.scale*factor))
        self.draw()
