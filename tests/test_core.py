import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from razorbeam_terraria.models import Monitor, dimension, resolution_warnings, suggest_layout, engine_settings
from razorbeam_terraria.displays import _settings_number_order
from razorbeam_terraria.locator import library_paths
from razorbeam_terraria import transactions as tx
from razorbeam_terraria import tmod
from razorbeam_terraria.native import SingleInstance

class Models(unittest.TestCase):
    def test_triple_excludes_top_and_retains_negative_origin(self):
        monitors=[Monitor('left',-2560,0,2560,1440),Monitor('middle',0,0,2560,1440,True),
                  Monitor('right',2560,0,2560,1440),Monitor('top',300,-1080,1920,1080)]
        layout=suggest_layout(monitors)
        self.assertEqual((layout['width'],layout['height'],layout['x'],layout['ui_x']),(7680,1440,-2560,2560))
        self.assertNotIn('top',layout['monitors'])

    def test_three_same_size_on_different_rows_are_not_triple(self):
        self.assertFalse(suggest_layout([Monitor(str(i),0,i*1440,2560,1440,i==0) for i in range(3)])['triple'])

    def test_single_ultrawide_is_not_split(self):
        result=suggest_layout([Monitor('ultrawide',0,0,5120,1440,True)])
        self.assertFalse(result['triple']);self.assertEqual(result['ui_width'],5120)

    def test_unusual_resolution_is_accepted_without_clamping(self):
        result=engine_settings(dimension('1'),dimension('10000'),0,0,False,0,0,1,10000)
        self.assertEqual((result['Width'],result['Height']),(1,10000))
        self.assertGreaterEqual(result['Cap'],10000);self.assertGreater(len(resolution_warnings(1,10000)),1)

    def test_invalid_dimensions_are_explained(self):
        for value in ('0','-1','1.5','abc','2147483648','999999999999','１'):
            with self.assertRaises(ValueError):dimension(value)

    def test_ui_bounds_and_isolated_centering(self):
        with self.assertRaises(ValueError):engine_settings(100,100,0,0,True,99,0,100,100)
        centered=engine_settings(300,100,0,0,True,100,0,100,100)
        self.assertEqual((centered['UiEnabled'],centered['UiX'],centered['UiWidth']),(1,100,100))
        self.assertEqual(engine_settings(1,10000,0,0,False,2560,0,2560,1440)['UiWidth'],1)
        with self.assertRaises(ValueError):engine_settings(100,100,0,0,False,0,0,100,100,center_splash=True,splash_x=99,splash_width=100,splash_height=100)
        self.assertEqual(engine_settings(100,100,0,0,False,0,0,100,100)['PreventMinimize'],0)
        self.assertEqual(engine_settings(100,100,0,0,False,0,0,100,100,prevent_minimize=True)['PreventMinimize'],1)

    def test_vdf_supports_modern_and_legacy_libraries(self):
        self.assertEqual(library_paths('"path" "I:\\\\Steam" "1" "G:\\\\Steam Games" "105600" "50000"'),['I:\\Steam','G:\\Steam Games'])

    def test_windows_display_ids_come_from_device_names(self):
        self.assertEqual(Monitor(r'\\.\DISPLAY12',0,0,1920,1080).display_id,12)

    def test_windows_settings_ids_use_connector_topology(self):
        records=[((0,1),10,2,581,0,r'\\.\DISPLAY1'),((0,1),10,1,579,1,r'\\.\DISPLAY4'),
                 ((0,1),10,0,577,2,r'\\.\DISPLAY3'),((0,1),5,0,576,3,r'\\.\DISPLAY2')]
        ids=_settings_number_order(records)
        self.assertEqual([ids[r'\\.\DISPLAY3'],ids[r'\\.\DISPLAY4'],ids[r'\\.\DISPLAY1'],ids[r'\\.\DISPLAY2']],[1,2,3,4])
        self.assertEqual(Monitor(r'\\.\DISPLAY1',0,0,1920,1080,True,3).display_id,3)

class NativeSafety(unittest.TestCase):
    def test_second_patcher_instance_is_detected(self):
        name='Local\\RazorbeamTerrariaPatcher.Test.'+next(tempfile._get_candidate_names())
        first=SingleInstance(name);second=SingleInstance(name)
        try:self.assertFalse(first.already_running);self.assertTrue(second.already_running)
        finally:second.close();first.close()

class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.game=self.root/'game';self.game.mkdir();self.exe=self.game/'Terraria.exe';self.exe.write_bytes(b'original fixture')
        self.backups=self.root/'backups';self.backups.mkdir();self.settings=engine_settings(7680,1440,-2560,0,False,0,0,7680,1440)
        self.env=patch.dict(os.environ,{"RAZORBEAM_TERRARIA_CONFIG":str(self.root/'config.json')});self.env.start()
    def tearDown(self):self.env.stop();self.temp.cleanup()
    @staticmethod
    def engine(*args):
        if args[0]=='patch':Path(args[2]).write_bytes(Path(args[1]).read_bytes()+b' patched')
        state='Vanilla' if Path(args[1]).read_bytes()==b'original fixture' else 'Razorbeam safe widescreen patch'
        return {'state':state,'version':'test','verified':True,'compatible':True}

    def test_no_backup_no_game_write(self):
        with self.assertRaises(ValueError):tx.patch(self.exe,'',self.settings)
        self.assertEqual(self.exe.read_bytes(),b'original fixture')

    def test_backup_inside_game_is_rejected(self):
        with self.assertRaises(ValueError):tx.backup_root(self.game,self.exe)

    def test_patch_and_restore_reuse_one_clean_backup(self):
        with patch.object(tx,'run_engine',side_effect=self.engine):
            result=tx.patch(self.exe,self.backups,self.settings)
            self.assertEqual(self.exe.read_bytes(),b'original fixture patched')
            self.assertEqual((Path(result['backup'])/'Terraria.exe').read_bytes(),b'original fixture')
            (self.backups/'RazorbeamTerrariaBackups/clean-backups.json').unlink()
            second=tx.patch(self.exe,self.backups,self.settings)
            self.assertEqual(second['backup'],result['backup']);self.assertFalse(second['clean_backup_created'])
            self.assertEqual(self.exe.read_bytes(),b'original fixture patched')
            restored=tx.restore(self.exe,self.backups,result['backup'])
            self.assertEqual(self.exe.read_bytes(),b'original fixture')
            self.assertEqual(restored['backup'],result['backup'])
            self.assertEqual(len(tx.list_backups(self.backups,self.exe)),1)
            registry=json.loads((self.backups/'RazorbeamTerrariaBackups/clean-backups.json').read_text())
            self.assertEqual(len(registry['installations']),1)

    def test_altered_executable_without_clean_backup_is_refused(self):
        self.exe.write_bytes(b'already altered')
        with patch.object(tx,'run_engine',side_effect=self.engine):
            with self.assertRaisesRegex(ValueError,'No verified clean'):
                tx.patch(self.exe,self.backups,self.settings)
        self.assertFalse((self.backups/'RazorbeamTerrariaBackups').exists())

    def test_corrupt_backup_never_restores(self):
        with patch.object(tx,'run_engine',side_effect=self.engine):result=tx.patch(self.exe,self.backups,self.settings)
        (Path(result['backup'])/'Terraria.exe').write_bytes(b'tampered')
        with self.assertRaises(ValueError):tx.restore(self.exe,self.backups,result['backup'])
        self.assertEqual(self.exe.read_bytes(),b'original fixture patched')

    def test_failed_engine_keeps_original_and_verified_backup(self):
        def fail(*args):
            if args[0]=='patch':raise RuntimeError('unsupported IL')
            return self.engine(*args)
        with patch.object(tx,'run_engine',side_effect=fail):
            with self.assertRaises(RuntimeError):tx.patch(self.exe,self.backups,self.settings)
        self.assertEqual(self.exe.read_bytes(),b'original fixture')
        self.assertEqual(len(tx.list_backups(self.backups,self.exe)),1)
        self.assertFalse(list(self.game.glob('.razorbeam-stage-*')))

    def test_changed_source_aborts_commit(self):
        def racing_engine(*args):
            result=self.engine(*args)
            if args[0]=='patch':self.exe.write_bytes(b'Steam update')
            return result
        with patch.object(tx,'run_engine',side_effect=racing_engine):
            with self.assertRaises(RuntimeError):tx.patch(self.exe,self.backups,self.settings)
        self.assertEqual(self.exe.read_bytes(),b'Steam update')

    def test_running_game_refused_before_any_backup(self):
        with patch.object(tx,'assert_game_closed',side_effect=RuntimeError('running')):
            with self.assertRaises(RuntimeError):tx.patch(self.exe,self.backups,self.settings)
        self.assertFalse((self.backups/'RazorbeamTerrariaBackups').exists())

    def test_other_installation_backup_refused(self):
        with patch.object(tx,'run_engine',side_effect=self.engine):result=tx.patch(self.exe,self.backups,self.settings)
        other=self.root/'other';other.mkdir();target=other/'Terraria.exe';target.write_bytes(b'other')
        with self.assertRaises(ValueError):tx.restore(target,self.backups,result['backup'])
        self.assertEqual(target.read_bytes(),b'other')

class TModLoaderTransactions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.install=self.root/'tModLoader';self.install.mkdir();(self.install/'tModLoader.dll').write_bytes(b'tml')
        (self.install/'tModLoader.deps.json').write_text('{"tModLoader/2026.6.3.6":{}}')
        self.save=self.root/'save';self.backups=self.root/'backups';self.backups.mkdir()
        self.bridge=self.root/'RazorbeamDisplay.tmod';self.bridge.write_bytes(b'bridge')
        self.env=patch.dict(os.environ,{"RAZORBEAM_TML_SAVE":str(self.save)});self.env.start()
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_install_enables_bridge_and_restore_removes_new_files(self):
        settings=engine_settings(7680,1440,-2560,0,True,2560,0,2560,1440,mode=1,prevent_minimize=True,skip_splash=True,diagnostics=True)
        with patch.object(tmod,'bundled_mod',return_value=self.bridge):result=tmod.patch(self.install,self.backups,settings)
        config=json.loads((self.save/'ModConfigs/RazorbeamDisplay_DisplayConfig.json').read_text())
        self.assertEqual(config['Width'],7680);self.assertTrue(config['CenteredUi']);self.assertEqual(config['UiX'],2560)
        self.assertTrue(config['PreventMinimize']);self.assertNotIn('SkipSplash',config);self.assertTrue(config['Diagnostics'])
        startup=json.loads((self.save/'config.json').read_text())
        self.assertEqual((startup['DisplayWidth'],startup['DisplayHeight']),(7680,1440))
        self.assertFalse(startup['WindowBorderless']);self.assertFalse(startup['ThrottleWhenInactive']);self.assertNotIn('QuickLaunch',startup)
        self.assertTrue(startup['RemoveForcedMinimumZoom']);self.assertEqual(startup['Zoom'],1.0)
        self.assertEqual(startup['UIScale'],1.0);self.assertFalse(startup['ResetDefaultUIScale'])
        self.assertIn('RazorbeamDisplay',json.loads((self.save/'Mods/enabled.json').read_text()))
        self.assertTrue(result['centered'])
        restored=tmod.restore(self.install,self.backups,result['backup'])
        self.assertTrue(restored['restored']);self.assertFalse((self.save/'Mods/RazorbeamDisplay.tmod').exists())
        self.assertFalse((self.save/'config.json').exists())

    def test_diagnostics_read_enabled_mods_and_useful_client_log_lines(self):
        (self.save/'Mods').mkdir(parents=True);(self.save/'Mods/enabled.json').write_text('["Zulu","Alpha"]',encoding='utf-8')
        logs=self.install/'tModLoader-Logs';logs.mkdir()
        (logs/'client.log').write_text('ordinary line\n[AIORP diagnostics] cursor\nWARN mod warning\n',encoding='utf-8')
        (logs/'terrariasteamclient.log').write_text('The connection to tML was closed unexpectedly. Look in client.log for details\n',encoding='utf-8')
        self.assertEqual(tmod.enabled_mods(self.install),['Alpha','Zulu'])
        self.assertEqual(tmod.diagnostic_log_lines(self.install),[
            '[AIORP diagnostics] cursor','WARN mod warning',
            '[AIORP diagnostics] termination=abnormal reason=TerrariaSteamClient reported an unexpected tModLoader disconnect'])

    def test_diagnostics_reports_unknown_termination_without_steam_client_log(self):
        logs=self.install/'tModLoader-Logs';logs.mkdir()
        (logs/'client.log').write_text('[AIORP diagnostics] cursor\n',encoding='utf-8')
        self.assertEqual(tmod.diagnostic_log_lines(self.install)[-1],
                         '[AIORP diagnostics] termination=unknown reason=TerrariaSteamClient log unavailable')

    def test_real_save_directory_comes_from_tmodloader_log(self):
        self.env.stop()
        actual=self.root/'redirected saves';logs=self.install/'tModLoader-Logs';logs.mkdir()
        (logs/'client.log').write_text('Starting tModLoader client 1.4.4.9+2026.07.3.0|stable\nSaves Are Located At: '+str(actual)+'\n',encoding='utf-8')
        self.assertEqual(tmod.save_dir(self.install),actual.resolve())
        self.assertEqual(tmod.version(self.install),'2026.7.3.0')
        self.env.start()

    def test_malformed_startup_config_aborts_without_installing_bridge(self):
        self.save.mkdir(); (self.save/'config.json').write_text('{broken',encoding='utf-8')
        settings=engine_settings(7680,1440,-2560,0,True,2560,0,2560,1440)
        with patch.object(tmod,'bundled_mod',return_value=self.bridge):
            with self.assertRaisesRegex(RuntimeError,'config.json is unreadable'):
                tmod.patch(self.install,self.backups,settings)
        self.assertEqual((self.save/'config.json').read_text(encoding='utf-8'),'{broken')
        self.assertFalse((self.save/'Mods/RazorbeamDisplay.tmod').exists())

    def test_failed_config_write_restores_every_original_file(self):
        files={
            'Mods/RazorbeamDisplay.tmod':b'old bridge',
            'Mods/enabled.json':b'["OldMod"]',
            'ModConfigs/RazorbeamDisplay_DisplayConfig.json':b'{"Enabled":false}',
            'config.json':b'{"DisplayWidth":1920}',
        }
        for name,data in files.items():
            path=self.save/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        settings=engine_settings(7680,1440,-2560,0,True,2560,0,2560,1440)
        real_atomic=tmod.atomic_json
        def fail_mod_config(path,value):
            if Path(path).name=='RazorbeamDisplay_DisplayConfig.json':raise OSError('disk full')
            return real_atomic(path,value)
        with patch.object(tmod,'bundled_mod',return_value=self.bridge),patch.object(tmod,'atomic_json',side_effect=fail_mod_config):
            with self.assertRaisesRegex(OSError,'disk full'):tmod.patch(self.install,self.backups,settings)
        for name,data in files.items():self.assertEqual((self.save/name).read_bytes(),data)

@unittest.skipUnless(os.environ.get('TERRARIA_TEST_EXE'),'Set TERRARIA_TEST_EXE to a locally owned executable; never redistributed')
class LocalExecutable(unittest.TestCase):
    def test_clean_source_patch_repatch_extreme_and_restore_actual_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game=root/'game';game.mkdir();exe=game/'Terraria.exe';shutil.copy2(os.environ['TERRARIA_TEST_EXE'],exe)
            backups=root/'backups';backups.mkdir();original=tx.digest(exe)
            with patch.dict(os.environ,{"RAZORBEAM_TERRARIA_CONFIG":str(root/'config.json')}):
                first=tx.patch(exe,backups,engine_settings(7680,1440,-2560,0,True,2560,0,2560,1440))
                self.assertTrue(first['centered']);self.assertTrue(first['verified']);self.assertEqual(first['settings']['Schema'],3)
                self.assertEqual(first['ui_layout'],'centered-interface')
                self.assertIn('Terraria-owned UI SpriteBatch lifecycle preserved',first['checks'])
                self.assertIn('UI logical dimensions follow selected display',first['checks'])
                self.assertIn('Every UI zoom reset preserves selected-display coordinates',first['checks'])
                second=tx.patch(exe,backups,engine_settings(1,10000,0,0,False,0,0,1,10000,mode=0,stable_title=False))
                self.assertEqual(second['settings']['Width'],1);self.assertEqual(second['settings']['Height'],10000)
                self.assertEqual(second['settings']['Mode'],0);self.assertFalse(second['centered']);self.assertEqual(second['settings']['StableTitle'],0)
                self.assertEqual(second['backup'],first['backup']);self.assertFalse(second['clean_backup_created'])
                third=tx.patch(exe,backups,engine_settings(1920,1080,0,0,False,0,0,1920,1080,mode=2))
                self.assertEqual(third['settings']['Mode'],2)
                tx.restore(exe,backups,first['backup']);self.assertEqual(tx.digest(exe),original)

if __name__=='__main__':unittest.main(verbosity=2)
