"""GUI acceptance tests. A real or virtual X display is needed on Linux."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from mercury.runtime import ensure_tk
from mercury.simulation import Flowsheet, PresetStore
from mercury.simulation.models import LEGEND_ID


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            ensure_tk()
            import tkinter as tk
            probe=tk.Tk(); probe.destroy()
            cls.tk=tk
        except Exception as error:
            raise unittest.SkipTest(f'Tk/display unavailable: {error}')

    def setUp(self):
        from mercury.gui.main_window import MainWindow
        self.temp=tempfile.TemporaryDirectory()
        self.root=self.tk.Tk()
        self.errors=[]
        self.root.report_callback_exception=lambda kind,value,tb:self.errors.append(value)
        self.app=MainWindow(self.root,PresetStore(Path(self.temp.name)/'presets.json'))
        self.pump(.2)

    def tearDown(self):
        self.app.closed=True
        if self.app._poll_id: self.root.after_cancel(self.app._poll_id)
        self.app.executor.shutdown(wait=True,cancel_futures=True)
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.errors,[])

    def pump(self,seconds=.05):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            self.root.update()
            time.sleep(.005)

    def run_and_wait(self):
        self.app.run_simulation()
        deadline=time.monotonic()+5
        while self.app.busy and time.monotonic()<deadline: self.pump(.02)
        self.assertFalse(self.app.busy)
        self.assertIsNotNone(self.app.result,self.app.status.get())

    def test_default_run_and_worker_responsiveness(self):
        app=self.app
        self.assertEqual(len(app.sheet.nodes),6)
        heartbeat=[]
        original=Flowsheet.simulate
        def slow(sheet,compressor_map):
            time.sleep(.15)
            return original(sheet,compressor_map)
        with patch.object(Flowsheet,'simulate',slow):
            self.root.after(25,lambda:heartbeat.append(True))
            app.run_simulation()
            self.assertTrue(app.busy)
            count=len(app.sheet.nodes)
            app.add_equipment('membrane')
            self.assertEqual(count,len(app.sheet.nodes))
            self.pump(.08)
            self.assertTrue(heartbeat)
            self.assertTrue(app.busy)
            self.pump(.25)
        self.assertIsNotNone(app.result)
        self.assertEqual(len(app.results_panel.table.get_children()),2)
        self.assertTrue(all(p.passed for p in app.result.products.values()))
        app.select('M-4')
        self.assertIn('0.129',app.editor.result_text.get('1.0','end'))
        app.select('P-1')
        self.assertIn('3,737',app.editor.result_text.get('1.0','end'))
        app.label_vars['pressure'].set(False); app.toggle_labels()
        self.assertNotIn('pressure',app.workspace.label_fields)
        app.reset_results()
        self.assertIsNone(app.result)
        self.assertEqual(len(app.sheet.nodes),6)

    def test_canvas_drag_connect_and_delete(self):
        app=self.app
        workspace=app.workspace
        canvas=workspace.canvas
        node=app.sheet.nodes['M-1']
        x=(node.x+workspace.WIDTH/2)*workspace.scale-canvas.canvasx(0)
        y=(node.y+workspace.HEIGHT/2)*workspace.scale-canvas.canvasy(0)
        old_x,old_y=node.x,node.y
        canvas.event_generate('<Button-1>',x=int(x),y=int(y))
        canvas.event_generate('<B1-Motion>',x=int(x+30),y=int(y+20))
        canvas.event_generate('<ButtonRelease-1>',x=int(x+30),y=int(y+20))
        self.pump()
        self.assertGreater(app.sheet.nodes['M-1'].x,old_x)
        self.assertGreater(app.sheet.nodes['M-1'].y,old_y)
        app.select('S-1'); app.delete_selected()
        self.assertNotIn('S-1',app.sheet.connections)
        for key,port,output in [('F-1','outlet',True),('C-1','inlet',False)]:
            x,y=workspace.port_position(app.sheet.nodes[key],port,output)
            canvas.event_generate('<Button-1>',x=int(x-canvas.canvasx(0)),y=int(y-canvas.canvasy(0)))
            self.pump()
        self.assertTrue(any(c.source_node=='F-1' and c.target_node=='C-1' for c in app.sheet.connections.values()))
        self.assertTrue(app.validate())
        app.select('M-2'); app.delete_selected()
        self.assertNotIn('M-2',app.sheet.nodes)
        self.assertFalse(any('M-2' in (c.source_node,c.target_node) for c in app.sheet.connections.values()))
        self.assertFalse(app.validate())
        self.assertTrue(app.workspace.error_nodes)

    def test_edit_properties_controls_custom_presets_and_invalidation(self):
        app=self.app
        self.run_and_wait()
        app.select('M-4')
        app.editor.variables['area_m2'].set('3000')
        app.editor.apply()
        self.assertEqual(app.sheet.nodes['M-4'].properties.area_m2,3000)
        self.assertIsNone(app.result)
        app.editor.variables['area_m2'].set('-3')
        app.editor.apply()
        self.assertEqual(app.sheet.nodes['M-4'].properties.area_m2,3000)
        self.assertIn('positive',app.editor.error.get())
        app.editor.variables['area_m2'].set('3100')
        with patch('mercury.gui.main_window.simpledialog.askstring',return_value='Pilot'):
            app.editor.save_preset()
        self.assertIn('Pilot',app.presets.names)
        self.assertEqual(app.sheet.nodes['M-4'].properties.preset,'Pilot')
        app.select('C-1')
        app.editor.variables['mode'].set('pressure')
        app.editor.variables['outlet_pressure_psia'].set('129.213')
        app.editor.apply()
        self.assertAlmostEqual(app.sheet.nodes['F-1'].properties.flow_slpm,17000)
        app.select('F-1')
        app.editor.variables['flow_slpm'].set('17500')
        app.editor.apply()
        self.assertEqual(app.sheet.nodes['C-1'].properties.mode,'flow')

    def test_save_load_clear_restore_and_bad_file_preserves_state(self):
        app=self.app
        path=Path(self.temp.name)/'test.json'
        app.select('M-1')
        app.editor.variables['area_m2'].set('4100')
        with patch('mercury.gui.main_window.filedialog.asksaveasfilename',return_value=str(path)):
            app.save()
        self.assertTrue(path.exists())
        self.assertFalse(app.dirty)
        snapshot=app.sheet.to_dict()
        app.clear()
        self.assertEqual(len(app.sheet.nodes),0)
        app.add_equipment('feed')
        app.add_equipment('splitter')
        app.add_equipment('mixer')
        self.assertEqual({n.kind for n in app.sheet.nodes.values()},{'feed','splitter','mixer'})
        app.load_path(path)
        self.assertEqual(app.sheet.to_dict(),snapshot)
        self.assertIsNone(app.result)
        invalid=Path(self.temp.name)/'bad.json'; invalid.write_text('{}')
        with self.assertRaises(ValueError): app.load_path(invalid)
        self.assertEqual(app.sheet.to_dict(),snapshot)
        app.restore_default()
        self.assertEqual(app.sheet.nodes['M-1'].properties.area_m2,4000)

    def test_startup_panes_and_reset_at_small_window_size(self):
        app=self.app
        self.assertTrue(app._layout_ready)
        for geometry in ('1050x740','1500x940'):
            self.root.geometry(geometry); self.pump()
            self.assertGreaterEqual(app.diagram_pane.winfo_width(),480)
            self.assertGreaterEqual(app.editor.winfo_width(),330)
            self.assertGreaterEqual(app.top_panes.winfo_height(),300)
            self.assertGreaterEqual(app.results_panel.winfo_height(),155)
        app.top_panes.sash_place(0,500,0)
        app.panes.sash_place(0,0,350)
        app.reset_view(); self.pump()
        self.assertGreater(app.diagram_pane.winfo_width(),900)
        self.assertGreaterEqual(app.results_panel.winfo_height(),155)

    def test_individual_stream_labels_and_temperature(self):
        app=self.app
        self.run_and_wait()
        edge=app.sheet.connections['P-1']
        expected={'temperature':'21','pressure':'14.70','flow':'3,737','oxygen':'0.4190'}
        for field,text in expected.items():
            for key,var in app.label_vars.items(): var.set(key==field)
            app.toggle_labels()
            label=app.workspace.stream_label(edge)
            self.assertIn(text,label)
            for other,other_text in expected.items():
                if other!=field: self.assertNotIn(other_text,label)
        app.select('P-1')
        self.assertIn('21 °C (assumed)',app.editor.result_text.get('1.0','end'))
        self.assertIn('0.419026',app.editor.result_text.get('1.0','end'))
        self.assertEqual(app.result.products['P-1'].stream.temperature_c,21)

    def test_temperature_edit_results_and_persistence(self):
        app = self.app
        self.run_and_wait()
        app.select('F-1')
        app.editor.variables['name'].set('Edited feed')
        app.temperature_var.set('25.5')
        app.apply_temperature()
        self.assertEqual(app.sheet.nodes['F-1'].name, 'Edited feed')
        self.assertEqual(app.sheet.temperature_c, 25.5)
        self.assertIsNone(app.result)
        self.assertTrue(app.dirty)
        edge = app.sheet.connections['P-1']
        self.assertEqual(app.workspace.stream_label(edge).splitlines()[0], '25.5')
        canvas = app.workspace.canvas
        texts = [canvas.itemcget(item, 'text') for item in canvas.find_all()
                 if canvas.type(item) == 'text']
        self.assertIn('Temperature assumed: 25.5 °C', texts)
        self.run_and_wait()
        app.select('P-1')
        self.assertIn('25.5 °C (assumed)', app.editor.result_text.get('1.0', 'end'))
        self.assertEqual(app.results_panel.table.set('P-1', 'temperature'), '25.5')
        app.temperature_var.set('24.25')
        app.file_path = Path(self.temp.name) / 'temperature.json'
        app.save()  # Save applies a pending temperature edit.
        self.assertEqual(Flowsheet.load(app.file_path).temperature_c, 24.25)
        app.load_path(app.file_path)
        self.assertEqual(app.temperature_var.get(), '24.25')
        app.temperature_var.set('22.5')
        self.run_and_wait()  # Run applies a pending edit, too.
        self.assertEqual(app.result.products['P-1'].stream.temperature_c, 22.5)
        app._set_busy(True)
        self.assertEqual(str(app.temperature_entry.cget('state')), 'disabled')
        app._set_busy(False)
        app._replace_sheet(Flowsheet())
        self.assertEqual(app.temperature_var.get(), '21')

    def test_invalid_and_unsaved_temperature(self):
        app = self.app
        self.run_and_wait()
        previous = app.result
        for value in ('', 'abc', 'nan', 'inf', '-273.15', '-300'):
            app.temperature_var.set(value)
            self.assertFalse(app.validate())
            self.assertEqual(app.sheet.temperature_c, 21)
            self.assertIs(app.result, previous)
            self.assertIn('Invalid temperature', app.status.get())
        app.temperature_var.set('25')
        app.dirty = False
        with patch('mercury.gui.main_window.messagebox.askyesno', return_value=False) as confirm:
            self.assertFalse(app._confirm_discard())
            confirm.assert_called_once()

    def test_context_add_outlet_role_and_empty_workspace_presets(self):
        app=self.app
        app.select('P-1'); app.delete_selected()
        menu=app.make_context_menu(('port','M-1','permeate',True),(10,20))
        menu.invoke(0); menu.destroy()  # Create oxygen product.
        edge=app.sheet.connections[app.selected]
        self.assertEqual(edge.outlet,'oxygen')
        self.assertTrue(edge.is_terminal)
        self.assertTrue(app.validate())
        self.run_and_wait()
        menu=app.make_context_menu(('edge',edge.id),(10,20))
        roles=menu.nametowidget(menu.entrycget(0,'menu'))
        roles.invoke(3); menu.destroy()  # Change terminal stream to purge.
        self.assertEqual(app.sheet.connections[edge.id].outlet,'vent')
        self.assertIsNone(app.result)
        self.run_and_wait()
        self.assertNotIn(edge.id,app.result.products)
        self.assertIn(edge.id,app.result.vents)
        self.assertLess(abs(app.result.flow_balance_error_slpm)/17500,1e-6)
        menu=app.make_context_menu(None,(812,412))
        presets=menu.nametowidget(menu.entrycget(0,'menu'))
        names=[presets.entrycget(i,'label') for i in range(presets.index('end')+1)]
        presets.invoke(names.index('N')); menu.destroy()
        node=app.sheet.nodes[app.selected]
        self.assertEqual((node.x,node.y),(812,412))
        self.assertEqual(node.properties,app.presets.get('N'))
        menu=app.make_context_menu(None,(900,500))
        menu.invoke(1); menu.destroy()
        self.assertEqual(app.sheet.nodes[app.selected].kind,'compressor')
        # Converting an internal stream leaves downstream equipment available
        # for reconnection, and exposes the missing inlet during validation.
        app.set_outlet('S-3','report')
        self.assertIn('M-2',app.sheet.nodes)
        self.assertTrue(any(i.node_id=='M-2' and 'inlet' in i.message for i in app.sheet.validate(app.compressor_map)))

    def _drag_item(self,kind,key,dx,dy):
        w=self.app.workspace; c=w.canvas
        item=next(item for item,hit in w.items.items() if hit[:2]==(kind,key))
        x1,y1,x2,y2=c.bbox(item)
        x,y=(x1+x2)/2-c.canvasx(0),(y1+y2)/2-c.canvasy(0)
        c.event_generate('<Button-1>',x=round(x),y=round(y))
        c.event_generate('<B1-Motion>',x=round(x+dx),y=round(y+dy))
        c.event_generate('<ButtonRelease-1>',x=round(x+dx),y=round(y+dy))
        self.pump()

    def test_drag_labels_resize_route_handles_and_save_geometry(self):
        app=self.app; w=app.workspace
        self.run_and_wait()
        result=app.result
        app.select('M-1')
        self._drag_item('resize','M-1',25,20)
        self.assertGreater(app.sheet.nodes['M-1'].width,170)
        self.assertGreater(app.sheet.nodes['M-1'].height,86)
        self._drag_item('node_label','M-1',25,-15)
        self.assertGreater(app.sheet.nodes['M-1'].label_dx,20)
        app.select('P-1')
        old_endpoint=app.sheet.connections['P-1'].endpoint
        self._drag_item('endpoint','P-1',30,15)
        self.assertGreater(app.sheet.connections['P-1'].endpoint[0],old_endpoint[0])
        self._drag_item('stream_label','P-1',25,20)
        self.assertGreater(app.sheet.connections['P-1'].label_dx,20)
        w.add_bend('P-1',(720,500))
        self.assertIn((720,500),app.sheet.connections['P-1'].waypoints)
        before=app.sheet.connections['P-1'].waypoints
        self._drag_item('bend','P-1',20,10)
        self.assertNotEqual(before,app.sheet.connections['P-1'].waypoints)
        before=app.sheet.connections['P-1'].waypoints
        self._drag_item('segment','P-1',25,25)
        self.assertNotEqual(before,app.sheet.connections['P-1'].waypoints)
        self.assertIs(app.result,result)  # Layout edits do not change physics.
        path=Path(self.temp.name)/'edited-layout.json'
        app.sheet.save(path)
        expected=app.sheet.to_dict()
        app.load_path(path)
        self.assertEqual(app.sheet.to_dict(),expected)
        app.select('M-1')
        app.editor.variables['width'].set('90'); app.editor.apply()
        self.assertIn('at least',app.editor.error.get())
        app.editor.variables['width'].set('260'); app.editor.apply()
        self.assertEqual(app.sheet.nodes['M-1'].width,260)

    def test_large_port_hit_area_and_canvas_context_coordinates(self):
        app=self.app; w=app.workspace; c=w.canvas
        app.select('S-1'); app.delete_selected()
        w.zoom(1.2)
        for key,port,output in [('F-1','outlet',True),('C-1','inlet',False)]:
            x,y=w.port_position(app.sheet.nodes[key],port,output)
            # Outside the visible port and old five-pixel target.
            c.event_generate('<Button-1>',x=round(x-c.canvasx(0)),y=round(y-c.canvasy(0)+13))
            self.pump()
        self.assertTrue(app.validate())
        with patch.object(app,'context_menu') as context:
            w.on_context=app.context_menu
            c.event_generate('<Button-3>',x=50,y=40)
            self.pump()
            self.assertEqual(context.call_count,1)
            point=context.call_args.args[1]
            self.assertAlmostEqual(point[0],c.canvasx(50)/w.scale)
            self.assertAlmostEqual(point[1],c.canvasy(40)/w.scale)

    def test_badge_geometry_values_and_warning_symbol(self):
        app=self.app; w=app.workspace; c=w.canvas
        label=w.stream_label(app.sheet.connections['P-1'])
        self.assertEqual(label.splitlines(),['21','—','—','—','O2 product'])
        self.run_and_wait()
        label=w.stream_label(app.sheet.connections['P-1'])
        self.assertEqual(label.splitlines(),['21','14.70','3,737','0.4190','O2 product'])
        for scale in (.3,.65,1.8):
            w.scale=scale; w.draw()
            for (key,metric),(shape,text,inner) in w.labels.badge_geometry.items():
                box=c.bbox(text)
                self.assertGreaterEqual(box[0],inner[0]-1)
                self.assertLessEqual(box[2],inner[2]+1)
                self.assertGreaterEqual(box[1],inner[1]-1)
                self.assertLessEqual(box[3],inner[3]+1)
                self.assertNotIn('\n',c.itemcget(text,'text'))
        app.sheet.connections['P-1']=replace(app.sheet.connections['P-1'],name='A very long stream name '*10)
        app._refresh()
        names=[c.itemcget(i,'text') for i,hit in w.items.items()
               if hit==('stream_label','P-1') and c.type(i)=='text']
        self.assertTrue(any(name.endswith('…') for name in names))
        app.set_outlet('P-1','nitrogen'); self.run_and_wait()
        self.assertFalse(app.result.products['P-1'].passed)
        self.assertIn('!',[c.itemcget(i,'text') for i,hit in w.items.items()
                          if hit==('stream_label','P-1') and c.type(i)=='text'])
        for var in app.label_vars.values(): var.set(False)
        app.toggle_labels()
        self.assertEqual(w.labels.badge_geometry,{})
        self.assertEqual(w.stream_label(app.sheet.connections['P-2']),'Purge 2')

    def test_textbox_creation_formatting_dragging_deletion_and_save(self):
        app=self.app; w=app.workspace; c=w.canvas
        self.run_and_wait(); result=app.result
        menu=app.make_context_menu(None,(450,710))
        index=next(i for i in range(menu.index('end')+1)
                   if menu.type(i)=='command' and menu.entrycget(i,'label')=='Add textbox')
        menu.invoke(index); menu.destroy(); self.pump()
        key=app.selected
        self.assertEqual((app.sheet.annotations[key].x,app.sheet.annotations[key].y),(450,710))
        self.assertIs(self.root.focus_get(),app.editor.entries['text'])
        editor=app.editor.entries['text']
        editor.delete('1.0','end'); editor.insert('1.0','Operating basis\nTwo-stage pilot')
        app.editor.variables['font_size'].set('14')
        app.editor.variables['bold'].set(True)
        with patch('mercury.gui.property_editor.colorchooser.askcolor',return_value=((18,52,86),'#123456')):
            app.editor._choose_color()
        app.editor.apply()
        annotation=app.sheet.annotations[key]
        self.assertEqual((annotation.font_size,annotation.bold,annotation.color),(14,True,'#123456'))
        self.assertEqual(annotation.text,'Operating basis\nTwo-stage pilot')
        w.fit(); self.pump()
        self._drag_item('annotation',key,25,-15)
        self.assertGreater(app.sheet.annotations[key].x,annotation.x)
        self._drag_item('annotation_resize',key,35,0)
        self.assertGreater(app.sheet.annotations[key].width,annotation.width)
        self.assertIs(app.result,result)
        # Double-click routes to the text editor, not the stream-bend action.
        item=next(i for i,hit in w.items.items() if hit==('annotation',key) and c.type(i)=='text')
        x1,y1,x2,y2=c.bbox(item)
        w._double_click(SimpleNamespace(x=(x1+x2)/2-c.canvasx(0),y=(y1+y2)/2-c.canvasy(0)))
        self.pump()
        self.assertIs(self.root.focus_get(),app.editor.entries['text'])
        text=app.editor.entries['text']; before=text.get('1.0','end-1c')
        text.mark_set('insert','1.0'); text.event_generate('<Delete>'); self.pump()
        self.assertIn(key,app.sheet.annotations)
        self.assertEqual(text.get('1.0','end-1c'),before[1:])
        app.editor.variables['width'].set('-1'); app.editor.apply()
        self.assertIn('positive',app.editor.error.get())
        app.editor.variables['width'].set('275')
        path=Path(self.temp.name)/'notes.json'
        with patch('mercury.gui.main_window.filedialog.asksaveasfilename',return_value=str(path)):
            app.save()
        saved=app.sheet.to_dict()
        app.load_path(path)
        self.assertEqual(app.sheet.to_dict(),saved)
        self.run_and_wait(); result=app.result
        app.select(key); app.delete_selected()
        self.assertNotIn(key,app.sheet.annotations)
        self.assertIs(app.result,result)
        # Invalid annotation metadata cannot replace the current workspace.
        invalid=Path(self.temp.name)/'invalid-notes.json'
        document=app.sheet.to_dict(); document['annotations']=[{'id':'TXT-bad','font_size':0}]
        invalid.write_text(json.dumps(document))
        current=app.sheet.to_dict()
        with self.assertRaises(ValueError): app.load_path(invalid)
        self.assertEqual(app.sheet.to_dict(),current)

    def test_legend_drag_reset_persistence_and_annotation_only_fit(self):
        app=self.app
        self.run_and_wait(); result=app.result
        original=app.sheet.legend
        self._drag_item('legend',LEGEND_ID,-35,15)
        self.assertLess(app.sheet.legend.x,original.x)
        self.assertGreater(app.sheet.legend.y,original.y)
        self.assertIs(app.result,result)
        path=Path(self.temp.name)/'legend.json'
        app.sheet.save(path)
        self.assertEqual(Flowsheet.load(path).legend,app.sheet.legend)
        menu=app.make_context_menu(('legend',LEGEND_ID),(0,0))
        menu.invoke(0); menu.destroy()
        self.assertEqual(app.sheet.legend,original)
        app._replace_sheet(Flowsheet())
        app.add_textbox((1100,900))
        app.workspace.fit(); self.pump()
        bounds=app.workspace.canvas.bbox('all')
        self.assertLess(bounds[2],app.workspace.canvas.winfo_width())
        self.assertLess(bounds[3],app.workspace.canvas.winfo_height())

    def _post_menu(self,hit=None,point=(100,100)):
        self.app.context_menu(hit,point,SimpleNamespace(x_root=400,y_root=260))
        self.pump()
        menu=self.app.context_menus.active
        self.assertIsNotNone(menu)
        self.assertTrue(menu.winfo_ismapped())
        return menu

    def _click_widget(self,widget,x=8,y=8):
        widget.event_generate('<Enter>',x=x,y=y)
        widget.event_generate('<ButtonPress-1>',x=x,y=y)
        widget.event_generate('<ButtonRelease-1>',x=x,y=y)
        self.pump()

    def test_posted_context_menus_dismiss_and_clicks_reach_widgets(self):
        app=self.app; c=app.workspace.canvas
        self.run_and_wait()
        app.select('M-1')
        menu=self._post_menu()
        self._click_widget(c,30,25)
        self.assertIsNone(app.context_menus.active)
        self.assertFalse(menu.winfo_exists())
        self.assertIsNone(app.selected)
        app.select('M-1'); entry=app.editor.entries['name']
        self._post_menu(); self._click_widget(entry)
        self.assertIsNone(app.context_menus.active)
        self.assertIs(self.root.focus_get(),entry)
        button=next(b for b in app.buttons if b.cget('text')=='Validate')
        self._post_menu(); self._click_widget(button)
        self.assertIsNone(app.context_menus.active)
        self.assertIn('valid',app.status.get())
        self._post_menu()
        table=app.results_panel.table
        x,y,width,height=table.bbox('P-1')
        self._click_widget(table,x+10,y+height/2)
        self.assertIsNone(app.context_menus.active)
        self.assertEqual(app.selected,'P-1')

    def test_posted_submenus_commands_escape_replacement_and_cleanup(self):
        app=self.app
        menu=self._post_menu()
        submenu=menu.nametowidget(menu.entrycget(0,'menu'))
        menu.activate(0); menu.tk.call(menu._w,'postcascade',0); self.pump()
        self.assertTrue(submenu.winfo_ismapped())
        # A press inside a submenu must not dismiss it before command dispatch.
        submenu.event_generate('<Enter>',x=10,y=10)
        submenu.event_generate('<ButtonPress-1>',x=10,y=10)
        self.assertIs(app.context_menus.active,menu)
        names=[submenu.entrycget(i,'label') for i in range(submenu.index('end')+1)]
        index=names.index('N'); submenu.activate(index)
        submenu.event_generate('<Motion>',x=10,y=submenu.yposition(index)+8)
        before=len(app.sheet.nodes)
        submenu.event_generate('<ButtonRelease-1>',x=10,y=submenu.yposition(index)+8)
        self.pump()
        self.assertEqual(len(app.sheet.nodes),before+1)
        self.assertEqual(app.sheet.nodes[app.selected].properties.preset,'N')
        self.assertIsNone(app.context_menus.active)
        self.assertFalse(menu.winfo_exists())
        menu=self._post_menu()
        submenu=menu.nametowidget(menu.entrycget(0,'menu'))
        menu.activate(0); menu.tk.call(menu._w,'postcascade',0); submenu.focus_force(); self.pump()
        submenu.event_generate('<Escape>'); self.pump()
        self.assertIsNone(app.context_menus.active)
        self.assertFalse(menu.winfo_exists())
        first=self._post_menu(); second=self._post_menu(); self.pump()
        self.assertFalse(first.winfo_exists())
        self.assertIs(app.context_menus.active,second)
        app._replace_sheet(Flowsheet()); self.pump()
        self.assertIsNone(app.context_menus.active)
        self.assertFalse(second.winfo_exists())
        manager=app.context_menus
        self._post_menu(); manager.close(); self.pump()
        self.assertTrue(manager.closed)
        self.assertEqual(manager.retired,{})
        self.assertEqual(self.root.bind_class(manager.tag,'<ButtonPress-1>'),'')


if __name__=='__main__': unittest.main()
