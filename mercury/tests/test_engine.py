import ast
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from scipy import optimize, stats

from mercury.simulation import (
    CompressorMap, CompressorProperties, Connection, EmptyProperties, Equipment,
    FeedProperties, Flowsheet, MembraneProperties, PresetStore, ProductProperties,
    SimulationError, SplitterProperties, Stream, default_flowsheet,
    TextAnnotation, LegendPosition,
)
from mercury.simulation.models import evaluate_product
from mercury.simulation.numerics import PROJECT_ROOT, checked_membrane, ideal_compressor_power
from mercury.simulation.units import psia_to_psig, psig_to_psia


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.presets = PresetStore(Path(cls.temp.name) / "presets.json")
        cls.map = CompressorMap()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_stream_and_pressure_units(self):
        s = Stream.binary("air", 100, .209, 14.7)
        self.assertAlmostEqual(s.composition["N2"], .791)
        self.assertEqual(s.temperature_c, 21)
        self.assertAlmostEqual(psia_to_psig(114.7), 100)
        self.assertAlmostEqual(psig_to_psia(100), 114.7)
        for flow, oxygen, pressure in [(-1,.2,14.7),(10,1.2,14.7),(10,.2,0),(float('nan'),.2,14.7)]:
            with self.assertRaises(ValueError):
                Stream.binary("bad", flow, oxygen, pressure)
        with self.assertRaises(ValueError):
            Stream("bad", 100, 14.7, {"O2": .2, "N2": .7})
        self.assertEqual(Stream.binary("zero", 0, .2, 14.7).flow_slpm, 0)

    def test_presets_and_custom_snapshots(self):
        p = self.presets.get("N")
        self.assertAlmostEqual(p.oxygen_permeance, .018390162805594107)
        self.assertAlmostEqual(p.alpha, 5.976608187134502)
        with tempfile.TemporaryDirectory() as tmp:
            store = PresetStore(Path(tmp)/"presets.json")
            custom = replace(p, area_m2=3000, alpha=7)
            store.save("Experiment", custom)
            self.assertEqual(PresetStore(store.path).get("Experiment").area_m2, 3000)
            with self.assertRaises(ValueError):
                store.save("O", custom)
            sheet = default_flowsheet(store)
            self.assertEqual(sheet.nodes["M-4"].properties, p)
            store.path.write_text('{invalid')
            broken = PresetStore(store.path)
            self.assertTrue(broken.warnings)
            self.assertIn("N", broken.names)
            with self.assertRaises(ValueError):
                broken.save("Safe", custom)
            self.assertEqual(store.path.read_text(), '{invalid')

    def test_temperature_assumption_propagation_and_power(self):
        sheet = default_flowsheet(self.presets)
        baseline = sheet.simulate(self.map)
        for temperature in (25, 21.5, -10):
            with self.subTest(temperature=temperature):
                sheet.temperature_c = temperature
                result = sheet.simulate(self.map)
                streams = list(result.streams.values())
                for equipment in result.equipment.values():
                    streams.extend(equipment.inlets.values())
                    streams.extend(equipment.outlets.values())
                self.assertEqual({s.temperature_c for s in streams}, {temperature})
                for key, stream in result.streams.items():
                    self.assertEqual(stream.flow_slpm, baseline.streams[key].flow_slpm)
                    self.assertEqual(stream.oxygen, baseline.streams[key].oxygen)
                self.assertAlmostEqual(result.equipment['C-1'].ideal_power_kw,
                                       94.55081992160665 * (temperature + 273.15) / 298.15)

    def test_temperature_persistence_and_validation(self):
        sheet = default_flowsheet(self.presets)
        sheet.temperature_c = 25.5
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'temperature.json'
            sheet.save(path)
            self.assertEqual(Flowsheet.load(path).temperature_c, 25.5)
        legacy = sheet.to_dict()
        del legacy['temperature_c']
        for version in (1, 2, 3):
            legacy['version'] = version
            self.assertEqual(Flowsheet.from_dict(legacy).temperature_c, 21)
        for value in (-273.15, -300, float('nan'), float('inf'), True, '25', None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    sheet.temperature_c = value
                self.assertEqual(sheet.temperature_c, 25.5)
                with self.assertRaises(ValueError):
                    Stream.binary('air', 100, .209, 14.7, temperature_c=value)
                with self.assertRaises(ValueError):
                    Flowsheet.from_dict({**sheet.to_dict(), 'temperature_c': value})

    def test_compressor_interpolation_and_inverse(self):
        for rpm in self.map.speeds:
            low, high = self.map.limits(rpm)
            for flow in [low, 10537.5, high]:
                forward = self.map.forward(flow, rpm, 14.7)
                self.assertAlmostEqual(self.map.inverse(forward['p_out'],rpm,14.7),flow,places=8)
            for flow in [-1, high+1]:
                with self.assertRaises(ValueError): self.map.forward(flow,rpm,14.7)
            for pressure in [14.7, 1000]:
                with self.assertRaises(ValueError): self.map.inverse(pressure,rpm,14.7)
        self.assertAlmostEqual(self.map.forward(17500,4500,14.7)['p_out'],123.627)
        self.assertAlmostEqual(ideal_compressor_power(17500,8.41),94.55081992160665)

    def test_linked_compressor_edits_are_transactional(self):
        s=default_flowsheet(self.presets)
        s.update_properties('C-1',CompressorProperties(4500,'pressure',outlet_pressure_psia=123.627),self.map)
        self.assertAlmostEqual(s.nodes['F-1'].properties.flow_slpm,17500)
        s.update_properties('C-1',replace(s.nodes['C-1'].properties,rpm=3500),self.map)
        flow=s.nodes['F-1'].properties.flow_slpm
        self.assertAlmostEqual(self.map.forward(flow,3500,14.7)['p_out'],123.627)
        s.update_properties('F-1',replace(s.nodes['F-1'].properties,pressure_psia=15),self.map)
        self.assertAlmostEqual(self.map.forward(s.nodes['F-1'].properties.flow_slpm,3500,15)['p_out'],123.627)
        s.update_properties('F-1',replace(s.nodes['F-1'].properties,flow_slpm=14000),self.map)
        self.assertEqual(s.nodes['C-1'].properties.mode,'flow')
        self.assertEqual(s.nodes['C-1'].properties.flow_slpm,14000)
        before=s.to_dict()
        with self.assertRaises(ValueError):
            s.update_properties('C-1',CompressorProperties(4500,'pressure',outlet_pressure_psia=1000),self.map)
        self.assertEqual(before,s.to_dict())

    def test_notebook_regression_default_and_other_orders(self):
        notebook=json.loads((PROJECT_ROOT/'membrane-data.ipynb').read_text())
        env={'np':np,'pd':pd,'optimize':optimize,'stats':stats,
             'comp':self.map.data,'RPM_LIST':list(self.map.speeds),
             'old_membrane':{'area':4000,'O2_perm':.031,'alpha':5.5},
             'new_membrane':{'area':self.presets.get('N').area_m2,
                             'O2_perm':self.presets.get('N').oxygen_permeance,
                             'alpha':self.presets.get('N').alpha},
             'O2_FLOW_SPEC':3400,'O2_PURITY_SPEC':.4,'N2_FLOW_SPEC':6000,'N2_O2_SPEC':.05,
             'STAGE_CUT_MIN':.2,'STAGE_CUT_MAX':.4}
        names={'membrane_stage','compressor','ideal_compressor_power','simulate_configuration'}
        functions=[]
        for cell in notebook['cells']:
            if cell['cell_type']=='code':
                functions.extend(n for n in ast.parse(''.join(cell['source'])).body
                                 if isinstance(n,ast.FunctionDef) and n.name in names)
        exec(compile(ast.Module(body=functions,type_ignores=[]),'<notebook reference>','exec'),env)
        for configuration in ['OOON','NOOO','ONOO','OONO']:
            sheet=default_flowsheet(self.presets)
            for i,label in enumerate(configuration,1): sheet.nodes[f'M-{i}'].properties=self.presets.get(label)
            result=sheet.simulate(self.map)
            reference, stages=env['simulate_configuration'](configuration,17500,4500)
            self.assertAlmostEqual(result.products['P-1'].stream.flow_slpm,reference['O2_product_flow'],places=8)
            self.assertAlmostEqual(result.products['R-4'].stream.oxygen,reference['N2_product_O2'],places=10)
            for i in range(1,5):
                for outlet in ['retentate','permeate']:
                    actual=result.equipment[f'M-{i}'].outlets[outlet]
                    self.assertAlmostEqual(actual.flow_slpm,stages.loc[i-1,outlet+'_flow'],places=8)
                    self.assertAlmostEqual(actual.oxygen,stages.loc[i-1,outlet+'_O2'],places=10)
            self.assertLess(abs(result.flow_balance_error_slpm),.001)
            self.assertLess(abs(result.oxygen_balance_error_slpm),.001)
        default=default_flowsheet(self.presets).simulate(self.map)
        self.assertTrue(all(p.passed for p in default.products.values()))
        self.assertTrue(any('M-4' in w for w in default.warnings))

    def test_topological_order_and_missing_ports(self):
        s=default_flowsheet(self.presets)
        s.nodes=dict(reversed(list(s.nodes.items())))
        r=s.simulate(self.map)
        self.assertLess(r.execution_order.index('C-1'),r.execution_order.index('M-1'))
        self.assertLess(r.execution_order.index('M-3'),r.execution_order.index('M-4'))
        s.delete('R-4')
        messages=[i.message for i in s.validate(self.map)]
        self.assertTrue(any('M-4 retentate outlet is not connected' in m for m in messages))
        with self.assertRaises(SimulationError): s.simulate(self.map)

    def test_connections_reject_implicit_split_merge_and_wrong_direction(self):
        s=default_flowsheet(self.presets)
        for c in [Connection('new','new','M-1','retentate','M-3','inlet'),
                  Connection('new','new','M-1','inlet','M-3','inlet'),
                  Connection('new','new','missing','outlet','M-3','inlet')]:
            with self.assertRaises(ValueError): s.connect(c)

    def test_cycles_are_explained(self):
        s=Flowsheet()
        for key in ['A','B']:
            s.add(Equipment(key,key,'compressor',CompressorProperties()))
        s.connect(Connection('1','1','A','outlet','B','inlet'))
        s.connect(Connection('2','2','B','outlet','A','inlet'))
        self.assertTrue(any('Recycle flowsheets' in i.message for i in s.validate(self.map)))
        with self.assertRaisesRegex(SimulationError,'Recycle'): s.topological_order()

    def test_split_mix_and_pressure_mismatch(self):
        s=Flowsheet(temperature_c=25.5)
        for node in [Equipment('F','F','feed',FeedProperties(10000,.209,100)),
                     Equipment('S','S','splitter',SplitterProperties(.3)),
                     Equipment('X','X','mixer',EmptyProperties()),
                     Equipment('P','P','product',ProductProperties())]: s.add(node)
        for c in [Connection('1','1','F','outlet','S','inlet'),Connection('2','2','S','a','X','a'),
                  Connection('3','3','S','b','X','b'),Connection('4','4','X','outlet','P','inlet')]: s.connect(c)
        r=s.simulate(self.map)
        self.assertAlmostEqual(r.streams['2'].flow_slpm,3000)
        self.assertAlmostEqual(r.products['P'].stream.flow_slpm,10000)
        self.assertAlmostEqual(r.products['P'].stream.oxygen,.209)
        self.assertEqual({stream.temperature_c for stream in r.streams.values()}, {25.5})
        s.delete('3')
        s.add(Equipment('F2','F2','feed',FeedProperties(3000,.5,99)))
        s.add(Equipment('V','V','vent',EmptyProperties()))
        s.connect(Connection('3','3','F2','outlet','X','b'))
        s.connect(Connection('5','5','S','b','V','inlet'))
        with self.assertRaisesRegex(SimulationError,'Mixer inlet pressures differ'): s.simulate(self.map)
        s.nodes['F2'].properties=replace(s.nodes['F2'].properties,pressure_psia=100)
        r=s.simulate(self.map)
        self.assertAlmostEqual(r.products['P'].stream.oxygen,(3000*.209+3000*.5)/6000)

    def test_membrane_branch_and_downstream_compressor_target(self):
        s=default_flowsheet(self.presets)
        s.delete('P-1')
        s.add(Equipment('C2','C2','compressor',CompressorProperties(2500)))
        s.connect(Connection('A','A','M-1','permeate','C2','inlet'))
        s.connect(Connection('B','B','C2','outlet',outlet='oxygen'))
        r=s.simulate(self.map)
        self.assertAlmostEqual(r.streams['A'].flow_slpm,r.streams['B'].flow_slpm)
        self.assertGreater(r.streams['B'].pressure_psia,r.streams['A'].pressure_psia)
        s.nodes['C2'].properties=CompressorProperties(2500,'flow',10000)
        with self.assertRaisesRegex(SimulationError,'adjust the upstream'): s.simulate(self.map)

    def test_numerical_failures_not_accepted(self):
        with self.assertRaisesRegex(ValueError,'positive feed'):
            checked_membrane(Stream.binary('zero',0,.209,100),self.presets.get('O'))
        with self.assertRaisesRegex(ValueError,'must exceed'):
            checked_membrane(Stream.binary('low',10000,.209,14.7),self.presets.get('O'))
        with self.assertRaisesRegex(ValueError,'residual'):
            checked_membrane(Stream.binary('low flow',125,.209,123.627),self.presets.get('O'))
        s=default_flowsheet(self.presets)
        with patch('mercury.simulation.flowsheet.checked_membrane',side_effect=RuntimeError('injected failure')):
            with self.assertRaisesRegex(SimulationError,'M-1 failed.*injected failure') as error:
                s.simulate(self.map)
            self.assertIn('Feed conditions',str(error.exception))

    def test_spec_boundary_and_generic_product(self):
        self.assertTrue(evaluate_product('O','oxygen',Stream.binary('O',3400,.4,14.7)).passed)
        self.assertTrue(evaluate_product('N','nitrogen',Stream.binary('N',6000,.05,100)).passed)
        self.assertFalse(evaluate_product('O','oxygen',Stream.binary('O',3399,.4,14.7)).passed)
        self.assertIsNone(evaluate_product('P','report',Stream.binary('P',1,.2,14.7)).passed)

    def test_save_load_and_invalid_documents(self):
        s=default_flowsheet(self.presets)
        s.nodes['M-1'].x=812.5
        s.nodes['M-4'].properties=replace(s.nodes['M-4'].properties,area_m2=3001,preset='snapshot')
        s.simulate(self.map)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sheet.json'
            s.save(path)
            loaded=Flowsheet.load(path)
            self.assertEqual(s.to_dict(),loaded.to_dict())
            self.assertNotIn('stage_cut',path.read_text())
            self.assertNotIn('results',path.read_text())
            loaded.delete('P-1')
            loaded.save(path)  # Drafts remain editable after reopening.
            self.assertTrue(Flowsheet.load(path).validate(self.map))
        for bad in [{'version':999}, {'version':1,'name':'bad','equipment':{},'connections':[]}]:
            with self.assertRaises(ValueError): Flowsheet.from_dict(bad)
        bad=s.to_dict(); bad['equipment'][0]['properties']['flow_slpm']=float('nan')
        with self.assertRaises(ValueError): Flowsheet.from_dict(bad)

    def test_terminal_stream_roles_balances_and_validation(self):
        sheet=default_flowsheet(self.presets)
        self.assertFalse(any(n.kind in ('product','vent') for n in sheet.nodes.values()))
        before=sheet.simulate(self.map)
        self.assertEqual(set(before.products),{'P-1','R-4'})
        self.assertEqual(set(before.vents),{'P-2','P-3','P-4'})
        for role in ['vent','report','nitrogen','oxygen']:
            sheet.connections['P-1']=replace(sheet.connections['P-1'],outlet=role)
            result=sheet.simulate(self.map)
            self.assertEqual(result.equipment,before.equipment)
            self.assertLess(abs(result.flow_balance_error_slpm)/17500,1e-6)
            self.assertLess(abs(result.oxygen_balance_error_slpm)/17500,1e-6)
            self.assertEqual({s.temperature_c for s in result.streams.values()},{21})
            if role=='vent': self.assertIn('P-1',result.vents)
            else: self.assertEqual(result.products['P-1'].role,role)
        invalid=[Connection('bad','bad','M-1','inlet',outlet='vent'),
                 Connection('bad','bad','missing','outlet',outlet='vent'),
                 Connection('bad','bad','M-1','permeate',outlet='invalid'),
                 Connection('bad','bad','M-1','permeate','M-2','inlet',outlet='vent')]
        for edge in invalid:
            with self.assertRaises(ValueError): sheet.connect(edge)
        feed=Flowsheet()
        feed.add(Equipment('F','F','feed',FeedProperties()))
        feed.connect(Connection('out','out','F','outlet',outlet='report'))
        feed.update_properties('F',FeedProperties(9000),self.map)
        self.assertEqual(feed.simulate(self.map).products['out'].stream.flow_slpm,9000)

    def test_geometry_roundtrip_and_legacy_outlet_migration(self):
        sheet=default_flowsheet(self.presets)
        before=sheet.simulate(self.map)
        sheet.nodes['M-1']=replace(sheet.nodes['M-1'],width=240,height=120,label_dx=15,label_dy=-70)
        sheet.connections['P-1']=replace(sheet.connections['P-1'],endpoint=(780,630),
                                        waypoints=((585,410),(780,410)),label_dx=30,label_dy=20)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'geometry.json'
            sheet.save(path)
            restored=Flowsheet.load(path)
            self.assertEqual(sheet.to_dict(),restored.to_dict())
            self.assertEqual(restored.simulate(self.map).equipment,before.equipment)
        for geometry in [{'endpoint':(float('nan'),0)}, {'waypoints':[[1,2,3]]}, {'label_dx':float('inf')}]:
            with self.assertRaises(ValueError): replace(sheet.connections['P-1'],**geometry)
        bad=sheet.to_dict(); bad['equipment'][0]['width']=0
        with self.assertRaises(ValueError): Flowsheet.from_dict(bad)
        # Construct the original v1 sink-node representation, including an
        # unfinished outlet placeholder that must not disappear on import.
        legacy=default_flowsheet(self.presets).to_dict()
        legacy['version']=1
        for node in legacy['equipment']:
            for key in ('width','height','label_dx','label_dy'): node.pop(key)
        for edge in legacy['connections']:
            if edge['outlet']:
                key='legacy-'+edge['id']
                legacy['equipment'].append(dict(id=key,name=edge['name'],
                    kind='vent' if edge['outlet']=='vent' else 'product',
                    properties={} if edge['outlet']=='vent' else {'role':edge['outlet']},
                    x=edge['endpoint'][0]-85,y=edge['endpoint'][1]-43))
                edge['target_node'],edge['target_port']=key,'inlet'
            for key in ('outlet','endpoint','waypoints','label_dx','label_dy'): edge.pop(key)
        migrated=Flowsheet.from_dict(legacy)
        self.assertEqual(len(migrated.nodes),6)
        result=migrated.simulate(self.map)
        self.assertEqual(result.products,before.products)
        self.assertEqual(result.vents,before.vents)
        self.assertEqual(migrated.to_dict()['version'],3)
        legacy['equipment'].append(dict(id='unfinished',name='Unconnected product',kind='product',properties={'role':'report'}))
        migrated=Flowsheet.from_dict(legacy)
        self.assertIn('unfinished',migrated.nodes)
        self.assertTrue(any(i.node_id=='unfinished' for i in migrated.validate(self.map)))

    def test_annotations_and_legend_roundtrip_without_affecting_simulation(self):
        sheet=default_flowsheet(self.presets)
        result=sheet.simulate(self.map)
        note=TextAnnotation('TXT-1','Operating basis\n21 °C',456,78,310,14,True,'#123abc')
        sheet.add_annotation(note)
        sheet.legend=LegendPosition(890,25)
        self.assertEqual(sheet.simulate(self.map),result)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'annotations.json'
            sheet.save(path)
            loaded=Flowsheet.load(path)
            self.assertEqual(loaded.to_dict(),sheet.to_dict())
            self.assertEqual(loaded.annotations[note.id],note)
            self.assertEqual(loaded.legend,sheet.legend)
        for version in (1,2):
            old=sheet.to_dict(); old['version']=version
            old.pop('annotations'); old.pop('legend')
            migrated=Flowsheet.from_dict(old)
            self.assertEqual(migrated.annotations,{})
            self.assertEqual(migrated.connections,sheet.connections)
            self.assertEqual(migrated.nodes,sheet.nodes)
            self.assertEqual(migrated.simulate(self.map),result)
        sheet.delete(note.id)
        self.assertEqual(sheet.annotations,{})
        self.assertEqual(sheet.simulate(self.map),result)

    def test_annotation_validation_and_shared_ids(self):
        for changes in [{'id':''},{'id':'@legend'},{'text':5},{'width':0},{'width':float('nan')},
                        {'x':float('inf')},{'font_size':0},{'font_size':1.5},{'bold':'yes'},
                        {'color':'red'},{'color':'#xyz123'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                TextAnnotation(**({'id':'TXT-1'}|changes))
        sheet=default_flowsheet(self.presets)
        for key in ('M-1','S-1'):
            with self.assertRaises(ValueError): sheet.add_annotation(TextAnnotation(key))
        sheet.add_annotation(TextAnnotation('TXT-1'))
        with self.assertRaises(ValueError): sheet.add_annotation(TextAnnotation('TXT-1'))
        with self.assertRaises(ValueError): sheet.add(Equipment('TXT-1','F','feed',FeedProperties()))
        with self.assertRaises(ValueError): sheet.connect(Connection('TXT-1','P','F-1','outlet',outlet='vent'))
        for changes in [{'annotations':{}},{'legend':{'x':float('nan'),'y':0}},
                        {'annotations':[{'id':'M-1'}]}, {'annotations':[{'id':'TXT-2','color':'bad'}]}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Flowsheet.from_dict(sheet.to_dict()|changes)


if __name__ == '__main__':
    unittest.main()
