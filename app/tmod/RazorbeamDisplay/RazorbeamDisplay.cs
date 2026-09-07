using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Newtonsoft.Json;
using Terraria;
using Terraria.GameInput;
using Terraria.GameContent.UI.States;
using Terraria.ModLoader;
using Terraria.ModLoader.Config;
using Terraria.ModLoader.Config.UI;
using Terraria.GameContent.UI.Elements;
using Terraria.Localization;
using Terraria.UI;

namespace RazorbeamDisplay;

public sealed class DisplayConfig : ModConfig
{
    public override ConfigScope Mode => ConfigScope.ClientSide;

    [JsonIgnore, ShowDespiteJsonIgnore, CustomModConfigItem(typeof(PatcherNoticeElement))]
    public bool PatcherNotice;

    // Bridge settings are written by the patcher. Keeping them non-public prevents
    // tModLoader and third-party config UIs from treating geometry as user options.
    [JsonProperty] [DefaultValue(true)] internal bool Enabled = true;
    [JsonProperty] [DefaultValue(1920)] [Range(1, int.MaxValue)] internal int Width = 1920;
    [JsonProperty] [DefaultValue(1080)] [Range(1, int.MaxValue)] internal int Height = 1080;
    [JsonProperty] [DefaultValue(0)] internal int WindowX;
    [JsonProperty] [DefaultValue(0)] internal int WindowY;
    [JsonProperty] [DefaultValue(1)] [Range(0, 2)] internal int WindowMode = 1;
    [JsonProperty] [DefaultValue(true)] internal bool StableTitle = true;
    [JsonProperty] [DefaultValue(false)] internal bool CenteredUi;
    [JsonProperty] [DefaultValue(0)] internal int UiX;
    [JsonProperty] [DefaultValue(0)] internal int UiY;
    [JsonProperty] [DefaultValue(1920)] [Range(1, int.MaxValue)] internal int UiWidth = 1920;
    [JsonProperty] [DefaultValue(1080)] [Range(1, int.MaxValue)] internal int UiHeight = 1080;
    [JsonProperty] [DefaultValue(false)] internal bool PreventMinimize;
    [JsonProperty] [DefaultValue(false)] internal bool Diagnostics;
}

// Informational only. Geometry remains private and managed by the companion app.
public sealed class PatcherNoticeElement : ConfigElement
{
    public override void OnBind()
    {
        base.OnBind();
        TextDisplayFunction = () => "";
        Height.Set(150f, 0f);
        UIText notice = new(Language.GetTextValue("Mods.RazorbeamDisplay.PatcherNotice"), 0.9f) {
            IsWrapped = true
        };
        notice.Width.Set(-24f, 1f);
        notice.Left.Set(12f, 0f);
        notice.Top.Set(12f, 0f);
        Append(notice);
    }
}

public sealed class DisplaySystem : ModSystem
{
    private static readonly MethodInfo UiScaleMatrixGetter = typeof(Main)
        .GetProperty(nameof(Main.UIScaleMatrix), BindingFlags.Public | BindingFlags.Static)?.GetGetMethod()
        ?? throw new MissingMethodException(typeof(Main).FullName, "get_UIScaleMatrix");
    private static readonly FieldInfo LastElementHoverField = typeof(UserInterface)
        .GetField("_lastElementHover", BindingFlags.NonPublic | BindingFlags.Instance);

    private delegate Matrix OrigUiScaleMatrix();
    private delegate Matrix HookUiScaleMatrix(OrigUiScaleMatrix orig);
    private delegate void OrigMatrixZoom(ref Matrix matrix);
    private delegate void HookMatrixZoom(OrigMatrixZoom orig, ref Matrix matrix);
    private delegate void OrigStaticUpdate(GameTime time);
    private delegate void HookStaticUpdate(OrigStaticUpdate orig, GameTime time);
    private delegate void OrigInputUpdate(object self);
    private delegate void HookInputUpdate(OrigInputUpdate orig, object self);
    private static long lastCompatibilityDiagnosticAt;

    private int delay = 90;
    private int positionDelay;
    private bool applied;
    private static int centeredUiDepth;
    private static int logicalClipDepth;
    private static bool cursorPairActive;
    private static int cursorPairMouseX;
    private static int cursorPairMouseY;
    private static int cursorThickBeforeX;
    private static int cursorThickBeforeY;
    private static int cursorThickAppliedX;
    private static int cursorThickAppliedY;
    private static long lastCursorDiagnosticAt;
    private static Mod owningMod;
    private static readonly Dictionary<UIState, string> CalculatedLayouts = new();
    private long lastDiagnosticAt;
    private string lastDiagnostic = "";
    private string lastDiagnosticButtons = "";
    private bool readyLogged;

    public override void Load()
    {
        if (Main.dedServ) return;
        owningMod = Mod;
        On_Main.DrawMenu += DrawMenuCentered;
        On_Main.DrawInterface += DrawInterfaceCentered;
        On_Main.DrawThickCursor += DrawThickCursorCentered;
        On_Main.DrawCursor += DrawCursorCentered;
        On_PlayerInput.SetZoom_UI += SetZoomUiCentered;
        On_UserInterface.Update += UserInterfaceUpdateCentered;
        On_UserInterface.GetDimensions += GetUiDimensionsCentered;
        On_UIElement.GetClippingRectangle += GetClippingRectangleCentered;
        On_UIGamepadHelper.CullPointsOutOfElementArea += CullPointsOutOfElementAreaCentered;
        MonoModHooks.Add(UiScaleMatrixGetter, (HookUiScaleMatrix)GetUiScaleMatrixCentered);
    }

    public override void Unload()
    {
        if (Main.dedServ) return;
        On_Main.DrawMenu -= DrawMenuCentered;
        On_Main.DrawInterface -= DrawInterfaceCentered;
        On_Main.DrawThickCursor -= DrawThickCursorCentered;
        On_Main.DrawCursor -= DrawCursorCentered;
        On_PlayerInput.SetZoom_UI -= SetZoomUiCentered;
        On_UserInterface.Update -= UserInterfaceUpdateCentered;
        On_UserInterface.GetDimensions -= GetUiDimensionsCentered;
        On_UIElement.GetClippingRectangle -= GetClippingRectangleCentered;
        On_UIGamepadHelper.CullPointsOutOfElementArea -= CullPointsOutOfElementAreaCentered;
        centeredUiDepth = 0;
        logicalClipDepth = 0;
        RestoreCursorPair();
        owningMod = null;
        CalculatedLayouts.Clear();
    }

    public override void PostUpdateInput()
    {
        if (Main.dedServ) return;
        if (!readyLogged) { Mod.Logger.Info("[AIORP] ready"); readyLogged = true; }
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!config.Enabled) return;

        if (delay > 0) { delay--; return; }
        if (!applied) {
            Apply(config);
            applied = true;
        }
        if (config.WindowMode != 2 && OperatingSystem.IsWindows() && ++positionDelay >= 45) {
            positionDelay = 0;
            ApplyWindow(config, Main.instance.Window.Handle);
        }
        if (config.StableTitle && Main.instance?.Window is not null)
            Main.instance.Window.Title = "Terraria";
        LogDiagnostics(config);
    }

    public override void PostSetupContent()
    {
        if (Main.dedServ) return;
        Mod.Logger.Info("[AIORP] bridge build=1.0.1");
        InstallCompatibilityHook("SilkyUIFramework", "SilkyUIFramework.Helper.PlayerInputHelper", "SetZoom",
            new[] { typeof(Matrix).MakeByRefType() }, true, (HookMatrixZoom)SilkyMatrixZoomCentered);
        InstallCompatibilityHook("SilkyUIFramework", "SilkyUIFramework.SilkyUIInputState", "Update",
            Type.EmptyTypes, false, (HookInputUpdate)SilkyInputUpdateCentered);
        InstallCompatibilityHook("ImproveGame", "ImproveGame.UIFramework.EventTriggerManager", "UpdateUI",
            new[] { typeof(GameTime) }, true, (HookStaticUpdate)ImproveGameUpdateCentered);
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!config.Diagnostics) return;
        string mods = string.Join(", ", ModLoader.Mods.OrderBy(mod => mod.Name)
            .Select(mod => $"{mod.Name} v{mod.Version} ({mod.DisplayName})"));
        Mod.Logger.Info($"[AIORP diagnostics] enabled mods ({ModLoader.Mods.Length}): {mods}");
        Mod.Logger.Info($"[AIORP diagnostics] config enabled={config.Enabled} surface=({config.Width}x{config.Height}) window=({config.WindowX},{config.WindowY}) mode={config.WindowMode} centered={config.CenteredUi} ui=({config.UiX},{config.UiY},{config.UiWidth},{config.UiHeight}) preventMinimize={config.PreventMinimize}");
    }

    private void InstallCompatibilityHook(string modName, string typeName, string methodName,
        Type[] parameters, bool isStatic, Delegate hook)
    {
        if (!ModLoader.TryGetMod(modName, out Mod target)) return;
        try {
            MethodInfo method = target.Code.GetType(typeName)?.GetMethod(methodName,
                BindingFlags.Public | BindingFlags.NonPublic | (isStatic ? BindingFlags.Static : BindingFlags.Instance),
                null, parameters, null);
            if (method is null || method.ReturnType != typeof(void))
                throw new MissingMethodException(typeName, methodName);
            MonoModHooks.Add(method, hook);
            Mod.Logger.Info($"[AIORP compatibility] installed {modName} v{target.Version}: {method}");
        }
        catch (Exception error) {
            Mod.Logger.Warn($"[AIORP compatibility] NOT installed {typeName}.{methodName}: {error}");
        }
    }

    private static void SilkyMatrixZoomCentered(OrigMatrixZoom orig, ref Matrix matrix)
    {
        orig(ref matrix);
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        // Only our translated UI matrix. Game-scale and identity layers retain
        // their own coordinate space. Silky's original helper ignores M41/M42.
        if (!ValidUi(config) || Math.Abs(matrix.M41 - config.UiX) > 0.01f ||
            Math.Abs(matrix.M42 - config.UiY) > 0.01f || matrix.M11 <= 0 || matrix.M22 <= 0)
            return;
        int beforeX = Main.mouseX, beforeY = Main.mouseY;
        // Preserve the original helper's scale/rounding; supply its missing origin.
        int offsetX = (int)(matrix.M41 / matrix.M11);
        int offsetY = (int)(matrix.M42 / matrix.M22);
        Main.mouseX -= offsetX;
        Main.mouseY -= offsetY;
        Main.lastMouseX -= offsetX;
        Main.lastMouseY -= offsetY;
        Main.screenWidth = Math.Max(1, (int)(config.UiWidth / matrix.M11));
        Main.screenHeight = Math.Max(1, (int)(config.UiHeight / matrix.M22));
        if (config.Diagnostics && Environment.TickCount64 - lastCompatibilityDiagnosticAt >= 1000) {
            lastCompatibilityDiagnosticAt = Environment.TickCount64;
            owningMod?.Logger.Info($"[AIORP compatibility] SilkyUI reset corrected before=({beforeX},{beforeY}) after=({Main.mouseX},{Main.mouseY}) translation=({matrix.M41},{matrix.M42}) scale=({matrix.M11},{matrix.M22}) depth={centeredUiDepth}");
        }
    }

    private static void ImproveGameUpdateCentered(OrigStaticUpdate orig, GameTime time)
    {
        if (!ValidUi(ModContent.GetInstance<DisplayConfig>())) { orig(time); return; }
        UiFieldSnapshot snapshot = new();
        centeredUiDepth++;
        try { PlayerInput.SetZoom_UI(); orig(time); }
        finally { centeredUiDepth--; snapshot.Restore(); }
    }

    private static void SilkyInputUpdateCentered(OrigInputUpdate orig, object self)
    {
        if (!ValidUi(ModContent.GetInstance<DisplayConfig>())) { orig(self); return; }
        UiFieldSnapshot snapshot = new();
        centeredUiDepth++;
        // Silky caches Main.mouseX/Y before hit testing, outside UserInterface.Update.
        try { PlayerInput.SetZoom_UI(); orig(self); }
        finally { centeredUiDepth--; snapshot.Restore(); }
    }

    private void LogDiagnostics(DisplayConfig config)
    {
        if (!config.Diagnostics) return;
        long now = Environment.TickCount64;
        string buttons = $"L{Main.mouseLeft}/R{Main.mouseRight}/M{Main.mouseMiddle}/X1{Main.mouseXButton1}/X2{Main.mouseXButton2}";
        bool buttonChanged = buttons != lastDiagnosticButtons;
        if (!buttonChanged && now - lastDiagnosticAt < 250) return;
        float scale = SafeUiScale();
        int logicalX = (int)((PlayerInput.MouseX - config.UiX) / scale);
        int logicalY = (int)((PlayerInput.MouseY - config.UiY) / scale);
        bool gameplayActive = !Main.gameMenu && Main.LocalPlayer?.active == true;
        UserInterface ui = gameplayActive ? Main.InGameUI : Main.MenuUI;
        UIElement hover = LastElementHoverField?.GetValue(ui) as UIElement;
        string hit = "none";
        if (hover is not null) {
            CalculatedStyle box = hover.GetDimensions();
            hit = $"{hover.GetType().FullName} box=({box.X:0.##},{box.Y:0.##},{box.Width:0.##},{box.Height:0.##}) hovering={hover.IsMouseHovering}";
        }
        string state = ui?.CurrentState?.GetType().FullName ?? "none";
        string current = $"gameMenu={Main.gameMenu} playerActive={Main.LocalPlayer?.active} inventory={Main.playerInventory} options={Main.ingameOptionsWindow} main=({Main.mouseX},{Main.mouseY}) logical=({logicalX},{logicalY}) playerInput=({PlayerInput.MouseX},{PlayerInput.MouseY}) mouseInfo=({PlayerInput.MouseInfo.X},{PlayerInput.MouseInfo.Y}) windowsClient={GetWindowsClientCursor()} preUI=({PlayerInput.PreUIX},{PlayerInput.PreUIY}) scale={scale:0.###} screen=({Main.screenWidth}x{Main.screenHeight}) ui=({config.UiX},{config.UiY},{config.UiWidth},{config.UiHeight}) scopeDepth={centeredUiDepth} buttons={buttons} consumed={Main.isMouseLeftConsumedByUI} mouseInterface={Main.LocalPlayer?.mouseInterface} state={state} hit={hit}";
        if (!buttonChanged && current == lastDiagnostic && now - lastDiagnosticAt < 2000) return;
        lastDiagnostic = current;
        lastDiagnosticButtons = buttons;
        lastDiagnosticAt = now;
        Mod.Logger.Info("[AIORP diagnostics] " + current);
    }

    private static bool ValidUi(DisplayConfig config) =>
        config.Enabled && config.CenteredUi && config.UiX >= 0 && config.UiY >= 0 &&
        config.UiWidth > 0 && config.UiHeight > 0 &&
        (long)config.UiX + config.UiWidth <= config.Width &&
        (long)config.UiY + config.UiHeight <= config.Height;

    private static string GetWindowsClientCursor()
    {
        if (!OperatingSystem.IsWindows() || Main.instance?.Window is null)
            return "n/a";
        NativePoint point;
        if (!GetCursorPos(out point) || !ScreenToClient(Main.instance.Window.Handle, ref point))
            return "unavailable";
        return $"({point.X},{point.Y})";
    }

    private static float SafeUiScale()
    {
        float scale = Main.UIScale;
        return float.IsNaN(scale) || float.IsInfinity(scale) || scale < 0.1f ? 1f : scale;
    }

    private readonly struct UiFieldSnapshot
    {
        private readonly int mouseX = Main.mouseX;
        private readonly int mouseY = Main.mouseY;
        private readonly int lastMouseX = Main.lastMouseX;
        private readonly int lastMouseY = Main.lastMouseY;
        private readonly int screenWidth = Main.screenWidth;
        private readonly int screenHeight = Main.screenHeight;

        public UiFieldSnapshot() { }

        public void Restore()
        {
            Main.mouseX = mouseX;
            Main.mouseY = mouseY;
            Main.lastMouseX = lastMouseX;
            Main.lastMouseY = lastMouseY;
            Main.screenWidth = screenWidth;
            Main.screenHeight = screenHeight;
        }
    }

    private static void ApplyUiMouse(DisplayConfig config)
    {
        float scale = SafeUiScale();
        Main.mouseX -= (int)(config.UiX / scale);
        Main.mouseY -= (int)(config.UiY / scale);
        Main.lastMouseX -= (int)(config.UiX / scale);
        Main.lastMouseY -= (int)(config.UiY / scale);
    }

    private static void ApplyUiFields(DisplayConfig config)
    {
        float scale = SafeUiScale();
        ApplyUiMouse(config);
        Main.screenWidth = Math.Max(1, (int)(config.UiWidth / scale));
        Main.screenHeight = Math.Max(1, (int)(config.UiHeight / scale));
    }

    private static void SetCursorMouseToSurfacePosition(DisplayConfig config)
    {
        float scale = SafeUiScale();
        Main.mouseX = (int)((PlayerInput.MouseX - config.UiX) / scale);
        Main.mouseY = (int)((PlayerInput.MouseY - config.UiY) / scale);
    }

    private static bool CursorUsesCenteredUi(DisplayConfig config) =>
        ValidUi(config) && (centeredUiDepth > 0 || Main.gameMenu);

    private static void RestoreCursorPair()
    {
        if (!cursorPairActive) return;
        Main.mouseX = cursorPairMouseX;
        Main.mouseY = cursorPairMouseY;
        cursorPairActive = false;
    }

    private static Vector2 DrawThickCursorCentered(On_Main.orig_DrawThickCursor orig, bool smart)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!CursorUsesCenteredUi(config))
            return orig(smart);

        // DrawThickCursor and DrawCursor form one logical draw. Do not restore
        // Main.mouseX/Y between them: another mod may observe or redraw the cursor
        // in the gap, producing two independently positioned cursor components.
        RestoreCursorPair();
        cursorPairMouseX = Main.mouseX;
        cursorPairMouseY = Main.mouseY;
        cursorThickBeforeX = Main.mouseX;
        cursorThickBeforeY = Main.mouseY;
        cursorPairActive = true;
        try {
            SetCursorMouseToSurfacePosition(config);
            cursorThickAppliedX = Main.mouseX;
            cursorThickAppliedY = Main.mouseY;
            Vector2 bonus = orig(smart);

            // A later-loaded cursor detour can restore the full-surface mouse
            // after drawing the outline. Reassert the local UI coordinate so the
            // fill and every cursor consumer between both calls see one position.
            SetCursorMouseToSurfacePosition(config);
            return bonus;
        }
        catch {
            RestoreCursorPair();
            throw;
        }
    }

    private static void DrawCursorCentered(On_Main.orig_DrawCursor orig, Vector2 bonus, bool smart)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!CursorUsesCenteredUi(config)) {
            RestoreCursorPair();
            orig(bonus, smart);
            return;
        }

        bool completesPair = cursorPairActive;
        int mouseX = Main.mouseX, mouseY = Main.mouseY;
        try {
            SetCursorMouseToSurfacePosition(config);
            if (config.Diagnostics) {
                long now = Environment.TickCount64;
                if (now - lastCursorDiagnosticAt >= 1000) {
                    lastCursorDiagnosticAt = now;
                    owningMod?.Logger.Info(
                        $"[AIORP cursor] gameMenu={Main.gameMenu} depth={centeredUiDepth} pair={completesPair} " +
                        $"raw=({PlayerInput.MouseX},{PlayerInput.MouseY}) " +
                        $"thickBefore=({cursorThickBeforeX},{cursorThickBeforeY}) " +
                        $"thickApplied=({cursorThickAppliedX},{cursorThickAppliedY}) " +
                        $"fillBefore=({mouseX},{mouseY}) fillApplied=({Main.mouseX},{Main.mouseY})");
                }
            }
            orig(bonus, smart);
        }
        finally {
            if (completesPair)
                RestoreCursorPair();
            else {
                Main.mouseX = mouseX;
                Main.mouseY = mouseY;
            }
        }
    }

    private static Matrix GetUiScaleMatrixCentered(OrigUiScaleMatrix orig)
    {
        Matrix matrix = orig();
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        return logicalClipDepth == 0 && ValidUi(config) && (centeredUiDepth > 0 || Main.gameMenu)
            ? matrix * Matrix.CreateTranslation(config.UiX, config.UiY, 0f)
            : matrix;
    }

    private static void DrawMenuCentered(On_Main.orig_DrawMenu orig, Main self, GameTime gameTime)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!ValidUi(config) || centeredUiDepth > 0) {
            orig(self, gameTime);
            return;
        }

        UiFieldSnapshot snapshot = new();
        centeredUiDepth++;
        try {
            PlayerInput.SetZoom_UI();
            orig(self, gameTime);
        }
        finally {
            centeredUiDepth--;
            snapshot.Restore();
        }
    }

    private static void DrawInterfaceCentered(On_Main.orig_DrawInterface orig, Main self, GameTime gameTime)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!ValidUi(config) || centeredUiDepth > 0) {
            orig(self, gameTime);
            return;
        }

        UiFieldSnapshot snapshot = new();
        centeredUiDepth++;
        try {
            // Start every UI scope from PlayerInput's immutable cached values.
            // SetZoom_UI invokes SetZoomUiCentered, which applies the origin once.
            PlayerInput.SetZoom_UI();
            orig(self, gameTime);
        }
        finally {
            centeredUiDepth--;
            snapshot.Restore();
        }
    }

    private static void UserInterfaceUpdateCentered(On_UserInterface.orig_Update orig, UserInterface self, GameTime gameTime)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!ValidUi(config)) {
            orig(self, gameTime);
            return;
        }
        if (centeredUiDepth > 0) {
            RecalculateUi(self, config);
            orig(self, gameTime);
            return;
        }

        UiFieldSnapshot snapshot = new();
        centeredUiDepth++;
        try {
            PlayerInput.SetZoom_UI();
            RecalculateUi(self, config);
            orig(self, gameTime);
        }
        finally {
            centeredUiDepth--;
            snapshot.Restore();
        }
    }

    private static void RecalculateUi(UserInterface ui, DisplayConfig config)
    {
        UIState state = ui?.CurrentState;
        if (state is null) return;
        string layout = $"local:{config.UiWidth}x{config.UiHeight}@{SafeUiScale():0.####}";
        if (CalculatedLayouts.TryGetValue(state, out string previous) && previous == layout) return;
        CalculatedLayouts[state] = layout;
        state.Recalculate();
    }

    private static void SetZoomUiCentered(On_PlayerInput.orig_SetZoom_UI orig)
    {
        orig();
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (ValidUi(config) && centeredUiDepth > 0)
            ApplyUiFields(config);
    }

    private static CalculatedStyle GetUiDimensionsCentered(On_UserInterface.orig_GetDimensions orig, UserInterface self)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!ValidUi(config)) return orig(self);
        float scale = SafeUiScale();
        return new CalculatedStyle(0f, 0f, config.UiWidth / scale, config.UiHeight / scale);
    }

    private static void CullPointsOutOfElementAreaCentered(On_UIGamepadHelper.orig_CullPointsOutOfElementArea orig, ref UIGamepadHelper self, SpriteBatch spriteBatch, List<SnapPoint> points, UIElement element)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!ValidUi(config) || centeredUiDepth == 0) {
            orig(ref self, spriteBatch, points, element);
            return;
        }

        // Gamepad culling compares logical SnapPoint coordinates against a clipping
        // rectangle. Suppress the physical centered-screen translation for this call.
        logicalClipDepth++;
        try { orig(ref self, spriteBatch, points, element); }
        finally { logicalClipDepth--; }
    }

    private static Rectangle GetClippingRectangleCentered(On_UIElement.orig_GetClippingRectangle orig, UIElement self, SpriteBatch spriteBatch)
    {
        if (centeredUiDepth == 0 || logicalClipDepth > 0) return orig(self, spriteBatch);
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        int screenWidth = Main.screenWidth, screenHeight = Main.screenHeight;
        float scale = SafeUiScale();
        try {
            Main.screenWidth = Math.Max(1, (int)(config.Width / scale));
            Main.screenHeight = Math.Max(1, (int)(config.Height / scale));
            return orig(self, spriteBatch);
        }
        finally {
            Main.screenWidth = screenWidth;
            Main.screenHeight = screenHeight;
        }
    }

    private static void Apply(DisplayConfig config)
    {
        bool fullscreen = config.WindowMode == 2;
        Main.screenBorderless = false;
        Main.SetDisplayMode(config.Width, config.Height, fullscreen);
        if (!fullscreen && OperatingSystem.IsWindows())
            ApplyWindow(config, Main.instance.Window.Handle);
    }

    private static void ApplyWindow(DisplayConfig config, IntPtr hwnd)
    {
        const int GWL_STYLE = -16;
        const long WS_BORDER = 0x00800000L, WS_DLGFRAME = 0x00400000L,
                   WS_THICKFRAME = 0x00040000L, WS_CAPTION = 0x00C00000L,
                   WS_MINIMIZEBOX = 0x00020000L, WS_MAXIMIZEBOX = 0x00010000L,
                   WS_SYSMENU = 0x00080000L;
        long style = GetWindowLongPtr(hwnd, GWL_STYLE).ToInt64();
        long decorated = WS_BORDER | WS_DLGFRAME | WS_THICKFRAME | WS_CAPTION |
                         WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU;
        style = config.WindowMode == 1 ? style & ~decorated : style | decorated;
        SetWindowLongPtr(hwnd, GWL_STYLE, new IntPtr(style));
        // SWP_NOZORDER keeps the game from forcing itself above other apps.
        SetWindowPos(hwnd, IntPtr.Zero, config.WindowX, config.WindowY,
            config.Width, config.Height, 0x0020 | 0x0010 | 0x0040 | 0x0004);
        if (config.PreventMinimize && IsIconic(hwnd))
            ShowWindow(hwnd, 4); // SW_SHOWNOACTIVATE
    }

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    private static extern IntPtr GetWindowLongPtr64(IntPtr hwnd, int index);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
    private static extern IntPtr GetWindowLongPtr32(IntPtr hwnd, int index);
    private static IntPtr GetWindowLongPtr(IntPtr hwnd, int index) =>
        IntPtr.Size == 8 ? GetWindowLongPtr64(hwnd, index) : GetWindowLongPtr32(hwnd, index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")]
    private static extern IntPtr SetWindowLongPtr64(IntPtr hwnd, int index, IntPtr value);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongW")]
    private static extern IntPtr SetWindowLongPtr32(IntPtr hwnd, int index, IntPtr value);
    private static IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr value) =>
        IntPtr.Size == 8 ? SetWindowLongPtr64(hwnd, index, value) : SetWindowLongPtr32(hwnd, index, value);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y,
        int width, int height, uint flags);
    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hwnd, int command);

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint
    {
        public int X;
        public int Y;
    }

    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out NativePoint point);
    [DllImport("user32.dll")]
    private static extern bool ScreenToClient(IntPtr hwnd, ref NativePoint point);
}
