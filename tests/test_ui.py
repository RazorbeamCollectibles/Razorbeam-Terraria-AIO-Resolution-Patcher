from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W, QtTest
from razorbeam_terraria.models import Monitor
from razorbeam_terraria import color_theme, window
from razorbeam_terraria.window import MainWindow

class Interface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=W.QApplication.instance() or W.QApplication([]);cls.app.setStyle('Fusion');color_theme.apply_application_theme(cls.app)
    def setUp(self):
        self.runtime=tempfile.TemporaryDirectory();self.runtime_patch=patch('razorbeam_terraria.window.APP_RUNTIME_DIR',Path(self.runtime.name));self.runtime_patch.start()
        self.output=Path(self.runtime.name)/'RazorbeamTerrariaAIORP';self.output.mkdir()
        monitors=[Monitor('L',-2560,0,2560,1440),Monitor('C',0,0,2560,1440,True),Monitor('R',2560,0,2560,1440),Monitor('T',320,-1080,1920,1080)]
        with patch('razorbeam_terraria.window.load_app_state',return_value={}),patch('razorbeam_terraria.window.detect_monitors',return_value=monitors),patch('razorbeam_terraria.window.default_output_dir',return_value=self.output):
            self.w=MainWindow(discover=False)
        self.w.show();QtTest.QTest.qWait(30)
    def tearDown(self):
        with patch.object(self.w,'save_state'):self.w.close()
        self.runtime_patch.stop();self.runtime.cleanup()
    def test_default_output_and_invalid_backup_gate(self):
        self.assertEqual(Path(self.w.backup.text()),self.output)
        self.assertFalse(self.w.desktop.isVisible())
        self.assertEqual(self.w.backup_note.text(),'');self.assertEqual(self.w.backup_note.height(),34)
        self.w.backup.setText(str(self.output/'missing'))
        with patch.object(self.w,'start_job') as job:
            self.w.patch_only.click();QtTest.QTest.qWait(100)
            self.assertIn('#ff0000',self.w.backup.styleSheet());self.assertFalse(self.w.desktop.isVisible());job.assert_not_called();tabs_y=self.w.tabs.y()
            QtTest.QTest.qWait(450);self.assertTrue(self.w.desktop.isVisible());self.assertEqual(self.w.tabs.y(),tabs_y)
            self.w.patch_only.click();QtTest.QTest.qWait(30);self.assertTrue(self.w.desktop.isVisible())
            self.w.desktop.click()
            self.assertTrue(self.w.backup.text());self.assertFalse(self.w.backup_bad)
    def test_launch_and_restore_also_require_backup(self):
        self.w.backup.setText(str(self.output/'missing'))
        with patch.object(self.w,'start_job') as job:
            self.w.begin_launch();self.w.choose_restore();job.assert_not_called()

    def test_open_buttons_open_their_directories(self):
        game=self.output/'game';game.mkdir();exe=game/'Terraria.exe';exe.write_bytes(b'x')
        self.w.game.setText(str(exe));self.w.backup.setText(str(self.output))
        with patch('razorbeam_terraria.window.G.QDesktopServices.openUrl',return_value=True) as opened:
            self.w.open_game_location();self.w.open_backup_location()
        self.assertEqual(opened.call_count,2)
        self.assertEqual(Path(opened.call_args_list[0].args[0].toLocalFile()).resolve(),game.resolve())
        self.assertEqual(Path(opened.call_args_list[1].args[0].toLocalFile()).resolve(),self.output.resolve())
    def test_clean_and_patched_terraria_launch_through_steam(self):
        self.w.target_kind='terraria';self.w.prevent_minimize.setChecked(False)
        with patch('razorbeam_terraria.window.G.QDesktopServices.openUrl',return_value=True) as steam:
            self.w.launch_game()
            steam.assert_called_once();steam.reset_mock()
            self.w.launch_game()
            steam.assert_called_once()

    def test_tmodloader_launches_through_steam(self):
        self.w.target_kind='tmodloader';self.w.diagnostics_mode.setChecked(True)
        self.w.skip_splash.setChecked(False)
        with patch('razorbeam_terraria.tmod.enabled_mods',return_value=['Alpha','Zulu']),patch('razorbeam_terraria.window.logging.info') as info,patch('razorbeam_terraria.window.G.QDesktopServices.openUrl',return_value=True) as steam:
            self.w.launch_game()
            self.assertIn('1281930',steam.call_args.args[0].toString())
        self.assertTrue(any('enabled tModLoader mods' in str(call) and 'Alpha, Zulu' in str(call) for call in info.call_args_list))

    def test_patch_buttons_name_and_select_explicit_targets(self):
        self.assertEqual(self.w.patch_play.text(),'Patch && Launch Terraria')
        self.assertEqual(self.w.patch_only.text(),'Patch && Launch tModLoader')
        with patch.object(self.w,'begin_patch') as begin:
            self.w.begin_patch_target('tmodloader')
            self.assertEqual(self.w.target_kind,'tmodloader');self.assertEqual(self.w.version_label.text(),'tModLoader Version: Detecting…');begin.assert_called_once_with(True)
    def test_manual_typing_cancels_search_and_stale_result_cannot_override(self):
        with patch.object(self.w,'stop_discovery') as stop:
            self.w.manual_game();stop.assert_called_once()
        self.w.game.setText('X:/manual/Terraria.exe')
        self.w.handle_result('locate',{'exe':'C:/other/Terraria.exe'},None)
        self.assertEqual(self.w.game.text(),'X:/manual/Terraria.exe')

    def test_failed_analysis_leaves_terminal_status(self):
        class FailedProcess:
            received=False
            events_file=None
            event_offset=0
            buffer=b''
            def readAllStandardOutput(self):return b''
        process=FailedProcess();events=Path(self.runtime.name)/'failed.events';process.events_file=events
        events.write_text('{"kind":"error","message":"Unsupported pre-release Razorbeam patch. Restore a clean backup or verify Terraria in Steam."}\n',encoding='utf-8')
        with patch.object(self.w,'fail'):
            self.w.read_messages(process,'analyze')
        self.assertEqual(self.w.version_label.text(),'Terraria Version: Restore required')
        self.assertIn('Restore a clean backup',self.w.analysis_text.text())
        self.assertTrue(self.w.analysis_text.isVisible())
    def test_three_modes_and_title_checkbox(self):
        self.assertEqual([self.w.mode.itemData(i) for i in range(3)],[1,0,2]);self.assertTrue(self.w.stable_title.isChecked());self.assertEqual(self.w.windowTitle(),'Razorbeam All-in-One Resolution Patcher for Terraria')
        self.assertEqual(self.w.stable_title.text(),'Disable title messages')
        self.assertEqual(self.w.skip_splash.text(),'Skip startup splash')
        self.assertEqual(self.w.center_splash.text(),'Center startup art on selected display')
        self.assertEqual(self.w.centered.text(),'Centered UI')
        self.assertTrue(self.w.centered.isEnabled());self.assertTrue(self.w.centered.isChecked());self.assertEqual(self.w.settings()['Width'],7680)
        self.w.centered.setChecked(True);settings=self.w.settings();self.assertEqual(settings['UiEnabled'],1)
        self.assertEqual((settings['UiX'],settings['UiY'],settings['UiWidth'],settings['UiHeight']),(2560,0,2560,1440))
        self.assertEqual(settings['PreventMinimize'],0)
        self.w.prevent_minimize.setChecked(True);self.assertEqual(self.w.settings()['PreventMinimize'],1)
        for key,value in {'ui_x':0,'ui_y':0,'ui_width':7680,'ui_height':1440}.items():self.w.ui_fields[key].setText(str(value))
        settings=self.w.settings();self.assertEqual((settings['UiX'],settings['UiWidth']),(2560,2560))
        self.assertFalse(self.w.center_splash.isEnabled());self.w.skip_splash.setChecked(False);self.assertTrue(self.w.center_splash.isEnabled())
    def test_standard_button_color_and_text_weight(self):
        self.assertEqual(color_theme.ui_color('button_background'),'#6802a7')
        stylesheet=color_theme.app_stylesheet()
        self.assertIn('background: #6802a7;',stylesheet)
        self.assertIn('font-weight: normal;',stylesheet)
        self.assertNotIn('font-weight: bold; padding',Path(window.__file__).read_text(encoding='utf-8'))
        self.assertIn('QCheckBox:disabled',stylesheet);self.assertIn('color: #777777;',stylesheet)
        migrated=color_theme.merged_known_colors({'ui':{'button_background':'#8a00c4'}})
        self.assertEqual(migrated['ui']['button_background'],'#6802a7')
    def test_tmodloader_supports_centered_ui(self):
        self.w.centered.setChecked(True);self.w.target.setCurrentIndex(self.w.target.findData('tmodloader'))
        self.assertTrue(self.w.centered.isEnabled());self.assertTrue(self.w.centered.isChecked());self.assertEqual(self.w.settings()['UiEnabled'],1)
        self.assertFalse(self.w.skip_splash.isEnabled());self.assertFalse(self.w.settings()['SkipSplash'])
        self.assertEqual(self.w.skip_splash._razorbeam_tooltip_filters[0].tooltip_text.strip(),'Cannot disable splash in tModLoader due to API conflict.')
    def test_centered_ui_choice_is_restored(self):
        self.w.state['settings']={**self.w.settings(),'UiEnabled':1,'UiX':2560,'UiY':0,'UiWidth':2560,'UiHeight':1440}
        self.w.centered.setChecked(False);self.w.apply_saved_settings()
        self.assertTrue(self.w.centered.isChecked())
    def test_nessa_requires_rapid_commit_and_stays_bottom_left(self):
        for _ in range(3):self.w.record_nessa_click()
        self.assertEqual(self.w.nessa_target,0)
        for _ in range(5):self.w.record_nessa_click()
        QtTest.QTest.qWait(280);self.assertGreater(self.w.nessa.getProgress(),0)
        self.assertEqual(self.w.nessa.x(),0);self.assertEqual(self.w.nessa.geometry().bottom(),self.w.patch_page.height()-1)
    def test_hide_log_tab(self):
        self.w.hide_log.setChecked(True);self.assertFalse(self.w.tabs.isTabVisible(self.w.tabs.indexOf(self.w.log_page)))
    def test_options_use_the_same_custom_tooltips(self):
        for widget in (self.w.diagnostics_mode,self.w.hide_log,self.w.centered):
            self.assertEqual(widget.toolTip(),'');self.assertEqual(len(widget._razorbeam_tooltip_filters),1)
        self.assertFalse(hasattr(self.w.prevent_minimize,'_razorbeam_tooltip_filters'))
        self.assertEqual(self.w.diagnostics_mode._razorbeam_tooltip_filters[0].tooltip_text.strip(),'Exported log contains verbose analytics.')
        self.assertEqual(self.w.stable_title._razorbeam_tooltip_filters[0].tooltip_text.strip(),'Keep window title "Terraria"')
        self.assertEqual(self.w.centered._razorbeam_tooltip_filters[0].tooltip_text.strip(),'Centers inventory, health bar, etc. to center of viewport')
        self.assertFalse(hasattr(self.w.skip_splash,'_razorbeam_tooltip_filters'))
        self.assertFalse(hasattr(self.w.center_splash,'_razorbeam_tooltip_filters'))
    def test_arbitrary_dimensions_preserved(self):
        self.w.centered.setChecked(False)
        self.w.width_edit.setText('1');self.w.height_edit.setText('10000')
        self.assertEqual((self.w.settings()['Width'],self.w.settings()['Height']),(1,10000))
    def test_footer_visibility(self):
        self.w.tabs.setCurrentIndex(2);self.assertTrue(self.w.print_log.isVisible());self.assertFalse(self.w.patch_only.isVisible())
        self.w.tabs.setCurrentIndex(3);self.assertTrue(self.w.show_all.isVisible());self.assertFalse(self.w.print_log.isVisible())
    def test_analyze_button_removed_and_log_exports_to_backup_output(self):
        self.assertFalse(hasattr(self.w,'analyze'));self.w.backup.setText(self.runtime.name)
        self.w.session_handler.lines.append('verbose diagnostics')
        with patch('PySide6.QtWidgets.QFileDialog.getExistingDirectory') as dialog:self.w.print_session();dialog.assert_not_called()
        self.assertEqual(len(list(Path(self.runtime.name).glob('Razorbeam Terraria Patcher log - *.txt'))),1)
    def test_diagnostics_export_includes_tmodloader_runtime_lines(self):
        self.w.backup.setText(self.runtime.name);self.w.target_kind='tmodloader';self.w.diagnostics_mode.setChecked(True)
        with patch('razorbeam_terraria.tmod.diagnostic_log_lines',return_value=['[AIORP diagnostics] cursor=(1,2)']):self.w.print_session()
        output=next(Path(self.runtime.name).glob('Razorbeam Terraria Patcher log - *.txt')).read_text(encoding='utf-8')
        self.assertIn('=== tModLoader diagnostics ===',output);self.assertIn('cursor=(1,2)',output)
    def test_launch_log_identifies_launcher_handoff_to_steam(self):
        self.w.target_kind='tmodloader';self.w.diagnostics_mode.setChecked(False)
        with patch('PySide6.QtGui.QDesktopServices.openUrl',return_value=True),self.assertLogs(level='INFO') as captured:
            self.w.launch_game()
        self.assertIn('tModLoader launch handed to Steam by Razorbeam AIO RP.',captured.output[-1])
    def test_monitor_click_selection_and_detected_layout_restore(self):
        target=self.w.monitors[-1];self.w.select_display(target.name)
        self.assertEqual((self.w.width_edit.text(),self.w.height_edit.text()),(str(target.width),str(target.height)))
        self.assertEqual(self.w.selected_display_names,[target.name])
        self.w.use_detected(False);self.assertEqual(self.w.width_edit.text(),'7680');self.assertEqual(len(self.w.selected_display_names),3)
    def test_monitor_drag_paints_multiple_displays(self):
        self.w.tabs.setCurrentWidget(self.w.monitor_map.parentWidget());self.app.processEvents();self.w.monitor_map.repaint();self.app.processEvents()
        boxes={name:rect.center() for rect,name in self.w.monitor_map.hit_boxes}
        press=G.QMouseEvent(C.QEvent.Type.MouseButtonPress,boxes['L'],boxes['L'],C.Qt.MouseButton.LeftButton,C.Qt.MouseButton.LeftButton,C.Qt.KeyboardModifier.NoModifier)
        move=G.QMouseEvent(C.QEvent.Type.MouseMove,boxes['R'],boxes['R'],C.Qt.MouseButton.NoButton,C.Qt.MouseButton.LeftButton,C.Qt.KeyboardModifier.NoModifier)
        release=G.QMouseEvent(C.QEvent.Type.MouseButtonRelease,boxes['R'],boxes['R'],C.Qt.MouseButton.LeftButton,C.Qt.MouseButton.NoButton,C.Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(self.w.monitor_map,press);self.app.sendEvent(self.w.monitor_map,move);self.app.sendEvent(self.w.monitor_map,release)
        self.assertEqual(set(self.w.selected_display_names),{'L','C','R'});self.assertEqual(self.w.width_edit.text(),'7680')
        self.w.select_displays(['L','C']);self.assertIn('bezel',self.w.display_note.text().lower())
    def test_nessa_uses_fixed_viewport_and_translates_whole_image(self):
        self.w.nessa.setProgress(.1);first=self.w.nessa.geometry()
        self.w.nessa.setProgress(.7);second=self.w.nessa.geometry()
        self.assertEqual(first,second);self.assertEqual(second.bottom(),self.w.patch_page.height()-1)

if __name__=='__main__':unittest.main(verbosity=2)
