using System;
using System.ComponentModel;
using System.Reflection;
using System.Runtime.InteropServices;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Terraria;
using Terraria.GameInput;
using Terraria.ModLoader;
using Terraria.ModLoader.Config;
using Terraria.UI;

namespace RazorbeamDisplay;

public sealed class DisplayConfig : ModConfig
{
    public override ConfigScope Mode => ConfigScope.ClientSide;

    [DefaultValue(true)] public bool Enabled = true;
    [DefaultValue(1920)] [Range(1, int.MaxValue)] public int Width = 1920;
    [DefaultValue(1080)] [Range(1, int.MaxValue)] public int Height = 1080;
    [DefaultValue(0)] public int WindowX;
    [DefaultValue(0)] public int WindowY;
    [DefaultValue(1)] [Range(0, 2)] public int WindowMode = 1;
    [DefaultValue(true)] public bool StableTitle = true;
    [DefaultValue(false)] public bool CenteredUi;
    [DefaultValue(0)] public int UiX;
    [DefaultValue(0)] public int UiY;
    [DefaultValue(1920)] [Range(1, int.MaxValue)] public int UiWidth = 1920;
    [DefaultValue(1080)] [Range(1, int.MaxValue)] public int UiHeight = 1080;
    [DefaultValue(false)] public bool PreventMinimize;
    [DefaultValue(false)] public bool SkipSplash;
}

public sealed class DisplaySystem : ModSystem
{
    private static readonly MethodInfo UiScaleMatrixGetter = typeof(Main)
        .GetProperty(nameof(Main.UIScaleMatrix), BindingFlags.Public | BindingFlags.Static)?.GetGetMethod()
        ?? throw new MissingMethodException(typeof(Main).FullName, "get_UIScaleMatrix");

    private static readonly FieldInfo AsyncLoadCompleteField = typeof(Main)
        .GetField("_isAsyncLoadComplete", BindingFlags.NonPublic | BindingFlags.Static);
    private static readonly FieldInfo QuickSplashField = typeof(Main)
        .GetField("quickSplash", BindingFlags.NonPublic | BindingFlags.Instance);
    private static readonly FieldInfo SplashCounterField = typeof(Main)
        .GetField("splashCounter", BindingFlags.NonPublic | BindingFlags.Instance);

    private delegate Matrix OrigUiScaleMatrix();
    private delegate Matrix HookUiScaleMatrix(OrigUiScaleMatrix orig);

    private int delay = 90;
    private int positionDelay;
    private bool applied;
    private static bool inCenteredUi;

    public override void Load()
    {
        if (Main.dedServ) return;
        On_Main.DrawInterface += DrawInterfaceCentered;
        On_Main.DrawSplash += DrawSplashSkipped;
        On_PlayerInput.SetZoom_UI += SetZoomUiCentered;
        On_UserInterface.Update += UserInterfaceUpdateCentered;
        On_UIElement.GetClippingRectangle += GetClippingRectangleCentered;
        MonoModHooks.Add(UiScaleMatrixGetter, (HookUiScaleMatrix)GetUiScaleMatrixCentered);
    }

    public override void Unload()
    {
        if (Main.dedServ) return;
        On_Main.DrawInterface -= DrawInterfaceCentered;
        On_Main.DrawSplash -= DrawSplashSkipped;
        On_PlayerInput.SetZoom_UI -= SetZoomUiCentered;
        On_UserInterface.Update -= UserInterfaceUpdateCentered;
        On_UIElement.GetClippingRectangle -= GetClippingRectangleCentered;
        inCenteredUi = false;
    }

    public override void PostUpdateInput()
    {
        if (Main.dedServ) return;
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
            Main.instance.Window.Title = "Razorbeam All-in-One Resolution Patcher for Terraria";
    }

    private static void DrawSplashSkipped(On_Main.orig_DrawSplash orig, Main self, GameTime gameTime)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (config.Enabled && config.SkipSplash &&
            AsyncLoadCompleteField is not null && QuickSplashField is not null && SplashCounterField is not null)
        {
            QuickSplashField.SetValue(self, true);
            bool loadingComplete = AsyncLoadCompleteField.GetValue(null) is true;
            SplashCounterField.SetValue(self, loadingComplete ? 200 : 125);
        }
        orig(self, gameTime);
    }

    private static bool ValidUi(DisplayConfig config) =>
        config.Enabled && config.CenteredUi && config.UiX >= 0 && config.UiY >= 0 &&
        config.UiWidth > 0 && config.UiHeight > 0 &&
        (long)config.UiX + config.UiWidth <= config.Width &&
        (long)config.UiY + config.UiHeight <= config.Height;

    private static float SafeUiScale()
    {
        float scale = Main.UIScale;
        return float.IsNaN(scale) || float.IsInfinity(scale) || scale < 0.1f ? 1f : scale;
    }

    private static void ApplyUiFields(DisplayConfig config)
    {
        float scale = SafeUiScale();
        Main.mouseX -= (int)(config.UiX / scale);
        Main.mouseY -= (int)(config.UiY / scale);
        Main.lastMouseX -= (int)(config.UiX / scale);
        Main.lastMouseY -= (int)(config.UiY / scale);
        Main.screenWidth = Math.Max(1, (int)(config.UiWidth / scale));
        Main.screenHeight = Math.Max(1, (int)(config.UiHeight / scale));
    }

    private static Matrix GetUiScaleMatrixCentered(OrigUiScaleMatrix orig)
    {
        Matrix matrix = orig();
        if (!inCenteredUi) return matrix;
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        return ValidUi(config)
            ? matrix * Matrix.CreateTranslation(config.UiX, config.UiY, 0f)
            : matrix;
    }

    private static void DrawInterfaceCentered(On_Main.orig_DrawInterface orig, Main self, GameTime gameTime)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (!ValidUi(config) || inCenteredUi) {
            orig(self, gameTime);
            return;
        }

        int mouseX = Main.mouseX, mouseY = Main.mouseY;
        int lastMouseX = Main.lastMouseX, lastMouseY = Main.lastMouseY;
        int screenWidth = Main.screenWidth, screenHeight = Main.screenHeight;
        inCenteredUi = true;
        try {
            ApplyUiFields(config);
            orig(self, gameTime);
        }
        finally {
            Main.mouseX = mouseX; Main.mouseY = mouseY;
            Main.lastMouseX = lastMouseX; Main.lastMouseY = lastMouseY;
            Main.screenWidth = screenWidth; Main.screenHeight = screenHeight;
            inCenteredUi = false;
        }
    }

    private static void SetZoomUiCentered(On_PlayerInput.orig_SetZoom_UI orig)
    {
        orig();
        if (!inCenteredUi) return;
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (ValidUi(config)) ApplyUiFields(config);
    }

    private static void UserInterfaceUpdateCentered(On_UserInterface.orig_Update orig, UserInterface self, GameTime gameTime)
    {
        DisplayConfig config = ModContent.GetInstance<DisplayConfig>();
        if (Main.gameMenu || !ValidUi(config) || inCenteredUi) {
            orig(self, gameTime);
            return;
        }

        int mouseX = Main.mouseX, mouseY = Main.mouseY;
        int lastMouseX = Main.lastMouseX, lastMouseY = Main.lastMouseY;
        int screenWidth = Main.screenWidth, screenHeight = Main.screenHeight;
        inCenteredUi = true;
        try {
            ApplyUiFields(config);
            orig(self, gameTime);
        }
        finally {
            Main.mouseX = mouseX; Main.mouseY = mouseY;
            Main.lastMouseX = lastMouseX; Main.lastMouseY = lastMouseY;
            Main.screenWidth = screenWidth; Main.screenHeight = screenHeight;
            inCenteredUi = false;
        }
    }

    private static Rectangle GetClippingRectangleCentered(On_UIElement.orig_GetClippingRectangle orig, UIElement self, SpriteBatch spriteBatch)
    {
        if (!inCenteredUi)
            return orig(self, spriteBatch);
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
}
