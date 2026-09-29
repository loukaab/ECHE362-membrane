"""Compact stream badges and their shared legend, drawn with native Canvas items."""

from tkinter import font as tkfont
import math

from mercury.simulation.models import LEGEND_ID


METRICS = ("temperature", "pressure", "flow", "oxygen")
UNITS = ("Temperature (°C)", "Pressure (psia)", "Flow (slpm)", "O₂ fraction (mol/mol)")


def stream_values(edge, result, fields):
    stream = result.streams.get(edge.id) if result else None
    values = ("21", f"{stream.pressure_psia:.2f}" if stream else "—",
              f"{stream.flow_slpm:,.0f}" if stream else "—",
              f"{stream.oxygen:.4f}" if stream else "—")
    return [(key, value) for key, value in zip(METRICS, values) if key in fields]


class DiagramLabels:
    def __init__(self, canvas, items):
        self.canvas, self.items = canvas, items
        self.fonts = {}
        self.badge_geometry = {}

    def font(self, scale, size=9, bold=False):
        key = (max(8, round(size*scale)), bold)
        if key not in self.fonts:
            font = tkfont.nametofont("TkDefaultFont", root=self.canvas).copy()
            font.configure(size=key[0], weight="bold" if bold else "normal")
            self.fonts[key] = font
        return self.fonts[key]

    def _register(self, item, hit):
        self.items[item] = hit
        return item

    def badge(self, kind, x, y, w, h, color, hit):
        """Text occupies the central rectangle, away from curved/notched ends."""
        c = self.canvas
        style = dict(fill="#f5f7fa", outline=color, width=1)
        if kind == "temperature":
            r = h/2
            points = []
            for cx,start in ((x+w-r,-90),(x+r,90)):
                for step in range(13):
                    angle = math.radians(start+step*15)
                    points.extend((cx+r*math.cos(angle),y+r+r*math.sin(angle)))
            shape = c.create_polygon(*points,**style)
        elif kind == "pressure":
            shape = c.create_polygon(x+8,y, x+w-8,y, x+w,y+h/2,
                                     x+w-8,y+h, x+8,y+h, x,y+h/2, **style)
        elif kind == "flow":
            shape = c.create_polygon(x,y, x+w,y, x+w-7,y+h/2,
                                     x+w,y+h, x,y+h, x+7,y+h/2, **style)
        else:
            shape = c.create_rectangle(x,y,x+w,y+h,**style)
        return self._register(shape, hit)

    def warning(self, x, y, hit):
        c = self.canvas
        self._register(c.create_polygon(x,y+12,x+7,y,x+14,y+12,
                                         fill="#f5c451",outline="#9d6d11"),hit)
        self._register(c.create_text(x+7,y+8,text="!",font=("TkDefaultFont",8,"bold"),fill="#5b420a"),hit)

    @staticmethod
    def shorten(text, font, width):
        text = " ".join(text.split())
        if font.measure(text) <= width:
            return text
        while text and font.measure(text+"…") > width:
            text = text[:-1]
        return text+"…"

    def stream(self, x, y, edge, result, fields, color, anchor, scale):
        c, font = self.canvas, self.font(scale)
        hit = ("stream_label", edge.id)
        values = stream_values(edge, result, fields)
        row_height = font.metrics("linespace")+2
        widths = [max(36, font.measure(value)+max(24,row_height+4)) for _,value in values]
        name_width = max(80, round(120*scale))
        name = self.shorten(edge.name, font, name_width)
        width = max([font.measure(name)] + widths)
        height = len(values)*(row_height+1)+font.metrics("linespace")+3
        left = x if anchor == "w" else x-width/2
        top = y-height/2 if anchor == "w" else y-height
        for i, ((key, value), w) in enumerate(zip(values, widths)):
            bx, by = left+(width-w)/2, top+i*(row_height+1)
            shape = self.badge(key,bx,by,w,row_height,color,hit)
            text = self._register(c.create_text(bx+w/2,by+row_height/2,text=value,
                                                font=font,fill=color),hit)
            self.badge_geometry[(edge.id,key)] = (shape,text,(bx+10,by,bx+w-10,by+row_height))
        self._register(c.create_text(left+width/2,top+height,anchor="s",text=name,
                                     font=font,fill=color),hit)
        product = result.products.get(edge.id) if result else None
        if product and product.passed is False:
            self.warning(left+width+4,top+height-14,hit)

    def legend(self, position, scale, selected=False):
        c, font = self.canvas, self.font(scale)
        x, y = position.x*scale, position.y*scale
        row = font.metrics("linespace")+8
        sample_width = 40
        note = "Temperature assumed: 21 °C"
        warning_text = "Product specification unmet"
        text_width = max(font.measure(s) for s in (*UNITS,warning_text))
        width = max(sample_width+text_width+28, font.measure(note)+20)
        height = row*5+font.metrics("linespace")+24
        hit = ("legend", LEGEND_ID)
        self._register(c.create_rectangle(x,y,x+width,y+height,fill="#fff",outline="#2363b5" if selected else "#9aa9ba",
                                           width=2 if selected else 1),hit)
        for i,(key,label) in enumerate(zip(METRICS,UNITS)):
            by = y+10+i*row
            self.badge(key,x+10,by,sample_width,row-4,"#61748a",hit)
            self._register(c.create_text(x+sample_width+20,by+(row-4)/2,text=label,
                                         anchor="w",font=font,fill="#243b55"),hit)
        by = y+10+4*row
        self.warning(x+23,by+2,hit)
        self._register(c.create_text(x+sample_width+20,by+(row-4)/2,text=warning_text,
                                     anchor="w",font=font,fill="#243b55"),hit)
        self._register(c.create_text(x+10,y+height-10,anchor="sw",text=note,font=font,fill="#61748a"),hit)
