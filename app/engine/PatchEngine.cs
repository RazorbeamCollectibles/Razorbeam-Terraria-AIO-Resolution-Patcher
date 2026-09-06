using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using Mono.Cecil;
using Mono.Cecil.Cil;

// Never writes the input. The Python transaction owner supplies a separate staging file.
internal static class PatchEngine {
    const string Tag = "Razorbeam.TerrariaPatch";
    const string CenteredInterface = "__Razorbeam_CenteredInterface";
    const string ApplyUiFields = "__Razorbeam_ApplyUiFields";
    const string ScopedUiZoom = "__Razorbeam_ScopedUiZoom";
    const string CenteredCraftingUpdate = "__Razorbeam_CenteredCraftingUpdate";
    static ModuleDefinition module;
    static TypeDefinition main, settings;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static readonly string[] Keys = { "Schema", "Width", "Height", "WindowX", "WindowY", "UiEnabled", "UiX", "UiY", "UiWidth", "UiHeight", "Cap", "Mode", "StableTitle",
        "SplashEnabled", "SplashX", "SplashY", "SplashWidth", "SplashHeight", "PreventMinimize" };
    static MethodDefinition Method(string name) {
        var found = main.Methods.Where(m => m.Name == name && m.HasBody).ToArray();
        Require(found.Length == 1, name + ": expected one method, found " + found.Length);
        return found[0];
    }
    static FieldDefinition Field(string name) {
        var f = main.Fields.Where(x => x.Name == name && x.IsStatic).ToArray();
        Require(f.Length == 1, "Missing or ambiguous static field: " + name);
        return f[0];
    }
    static void Require(bool ok, string message) { if (!ok) throw new InvalidOperationException(message); }
    static bool IsField(Instruction i, string name) {
        var f = i.Operand as FieldReference;
        return f != null && f.DeclaringType.FullName == main.FullName && f.Name == name;
    }
    static int? Number(Instruction i) {
        if (i == null) return null;
        if (i.OpCode == OpCodes.Ldc_I4) return (int)i.Operand;
        if (i.OpCode == OpCodes.Ldc_I4_S) return (sbyte)i.Operand;
        if (i.OpCode == OpCodes.Ldc_I4_M1) return -1;
        if (i.OpCode.Code >= Code.Ldc_I4_0 && i.OpCode.Code <= Code.Ldc_I4_8)
            return (int)i.OpCode.Code - (int)Code.Ldc_I4_0;
        return null;
    }
    static Instruction StoreValue(MethodDefinition method, string name, Func<int,bool> accept) {
        var a = method.Body.Instructions.Where(i => i.OpCode == OpCodes.Stsfld && IsField(i, name)
            && Number(i.Previous).HasValue && accept(Number(i.Previous).Value)).ToArray();
        Require(a.Length == 1, method.Name + " -> " + name + ": expected one recognized assignment, found " + a.Length);
        return a[0].Previous;
    }
    static void SetInt(Instruction i, int v) { i.OpCode = OpCodes.Ldc_I4; i.Operand = v; }
    static IEnumerable<MethodDefinition> AllMethods(TypeDefinition t) {
        return t.Methods.Concat(t.NestedTypes.SelectMany(AllMethods));
    }
    static MethodReference Ref(string type, string name, int argc) {
        var refs = module.Types.SelectMany(AllMethods).Where(m=>m.HasBody).SelectMany(m=>m.Body.Instructions)
            .Select(i=>i.Operand as MethodReference).Where(m=>m != null && m.DeclaringType.FullName == type && m.Name == name && m.Parameters.Count == argc).ToArray();
        Require(refs.Length > 0, "Missing framework reference " + type + "::" + name);
        return refs[0];
    }
    static Instruction[] Calls(MethodDefinition m, string name) {
        return m.Body.Instructions.Where(i => (i.OpCode == OpCodes.Call || i.OpCode == OpCodes.Callvirt)
            && i.Operand is MethodReference && ((MethodReference)i.Operand).DeclaringType.FullName == main.FullName
            && ((MethodReference)i.Operand).Name == name).ToArray();
    }
    static Instruction[] Calls(MethodDefinition m, string type, string name) {
        return m.Body.Instructions.Where(i => (i.OpCode == OpCodes.Call || i.OpCode == OpCodes.Callvirt)
            && i.Operand is MethodReference && ((MethodReference)i.Operand).DeclaringType.FullName == type
            && ((MethodReference)i.Operand).Name == name).ToArray();
    }
    static Instruction InitCap(int expected) {
        var a = Method("InitTargets").Body.Instructions.Where(i=>Number(i)==expected
            && i.Next != null && i.Next.OpCode.Name.StartsWith("stloc")
            && i.Previous != null && i.Previous.OpCode.FlowControl == FlowControl.Cond_Branch
            && Number(i.Previous.Previous)==1 && IsField(i.Previous.Previous.Previous, "_selectedGraphicsProfile")).ToArray();
        Require(a.Length == 1, "InitTargets: expected one HiDef render-target cap, found " + a.Length);
        return a[0];
    }
    static Instruction FrameworkCap(int expected) {
        var m=Method("TrySupporting8K");
        Require(m.Body.Instructions.Any(i=>i.OpCode==OpCodes.Ldstr && (string)i.Operand=="MaxTextureSize"), "Unrecognized XNA capability code");
        var a=m.Body.Instructions.Where(i=>Number(i)==expected && i.Next!=null && i.Next.OpCode==OpCodes.Box
            && i.Next.Next!=null && i.Next.Next.Operand is MethodReference && ((MethodReference)i.Next.Next.Operand).Name=="SetValue").ToArray();
        Require(a.Length==1, "TrySupporting8K: ambiguous XNA profile cap");
        return a[0];
    }
    static Dictionary<string,int> ReadSettings() {
        var result=new Dictionary<string,int>();
        if(settings==null) return result;
        Require(settings.Methods.Count(m=>m.Name==".cctor")==1,"Invalid Razorbeam marker");
        var c=settings.Methods.Single(m=>m.Name==".cctor");
        var schemaAssignment=c.Body.Instructions.SingleOrDefault(i=>i.OpCode==OpCodes.Stsfld && i.Operand is FieldReference && ((FieldReference)i.Operand).Name=="Schema");
        Require(schemaAssignment!=null && Number(schemaAssignment.Previous).HasValue,"Invalid setting Schema");
        Require(Number(schemaAssignment.Previous).Value==3,"Unsupported pre-release Razorbeam patch. Restore a clean backup or verify Terraria in Steam.");
        foreach(var key in Keys) {
            var assignments=c.Body.Instructions.Where(i=>i.OpCode==OpCodes.Stsfld && i.Operand is FieldReference && ((FieldReference)i.Operand).Name==key).ToArray();
            Require(assignments.Length==1 && Number(assignments[0].Previous).HasValue,"Invalid setting " + key);
            result[key]=Number(assignments[0].Previous).Value;
        }
        return result;
    }
    static void CheckWindowAnchors() {
        var load=Method("LoadSettings");
        var flags=load.Body.Instructions.Where(i=>i.OpCode==OpCodes.Ldsfld && IsField(i,"screenBorderless")
            && i.Next!=null && i.Next.OpCode==OpCodes.Stsfld && IsField(i.Next,"PendingBorderlessState")).ToArray();
        Require(flags.Length==1,"LoadSettings: window flags anchor changed");
        var h=load.Body.Instructions.Where(i=>i.OpCode==OpCodes.Ldstr && (string)i.Operand=="DisplayHeight").ToArray();
        Require(h.Length==1 && h[0].Next.Next.OpCode==OpCodes.Callvirt,"LoadSettings: resolution anchor changed");
        if(settings==null) Require(h[0].Next.Next.Next.OpCode.Name.StartsWith("ldloc") && h[0].Next.Next.Next.Next.OpCode.Name.StartsWith("ldloc"),"LoadSettings: resolution locals changed");
        else Require(h[0].Next.Next.Next.OpCode==OpCodes.Ldsfld && ((FieldReference)h[0].Next.Next.Next.Operand).FullName==Setting("Width").FullName,"Custom resolution initialization missing");
        Require(Method("SetDisplayModeAsBorderless").Parameters.Count==3,"Borderless signature changed");
        Require(Method("SetBorderlessFormStyle").Parameters.Count==1,"Window-style signature changed");
    }
    static Dictionary<string,object> Analyze() {
        Require(module.Assembly.Name.Name=="Terraria","Only vanilla Terraria assemblies are supported (not tModLoader/server)");
        var old=ReadSettings();
        CheckWindowAnchors();
        var wide=StoreValue(Method(".cctor"),"SupportWideScreen",x=>x==0||x==1);
        int cap=old.Count>0 ? old["Cap"] : 4096;
        var values=new Dictionary<string,int>();
        foreach(var f in new[]{"maxScreenW","maxScreenH","_renderTargetMaxSize"}) {
            var i=StoreValue(Method("SetGraphicsProfileInternal"),f,x=>old.Count>0 ? x==cap : x==4096||x==8192);
            values[f]=Number(i).Value;
        }
        Require(values.Values.Distinct().Count()==1,"Graphics caps disagree; partial or unsupported patch");
        cap=values.Values.First();
        InitCap(cap);
        FrameworkCap(old.Count>0 ? cap : 8192);
        if(old.Count>0) {
            var wrapper=Method("__Razorbeam_DrawSplash");
            Require(wrapper.Body.ExceptionHandlers.Count==1 && wrapper.Body.ExceptionHandlers[0].HandlerType==ExceptionHandlerType.Finally,"Splash viewport restore/finally is missing");
            Require(Calls(wrapper,"DrawSplash").Length==2,"Splash wrapper original call changed");
            Require(Calls(Method("DoDraw"),"__Razorbeam_DrawSplash").Length==1,"Splash wrapper call site changed");
            var centered=main.Methods.Where(m=>m.Name==CenteredInterface && m.HasBody).ToArray();
            Require(centered.Length<=1,"Ambiguous centered-interface wrapper");
            Require(old["UiEnabled"]==0 || centered.Length==1,"Centered UI setting exists without its isolated wrapper");
            if(centered.Length==1) {
                Require(centered[0].Body.ExceptionHandlers.Count==1 && centered[0].Body.ExceptionHandlers[0].HandlerType==ExceptionHandlerType.Finally,"Centered-interface restore/finally is missing");
                Require(Calls(centered[0],"DrawInterface").Length==2,"Centered-interface original call changed");
                Require(Calls(Method("DoDraw"),CenteredInterface).Length==1,"Centered-interface call site changed");
            } else Require(Calls(Method("DoDraw"),"DrawInterface").Length==1,"DoDraw -> DrawInterface: expected one call");
        } else Require(Calls(Method("DoDraw"),"DrawSplash").Length==1,"DoDraw -> DrawSplash: expected one call");
        bool hasCentered=main.Methods.Count(m=>m.Name==CenteredInterface && m.HasBody)==1;
        bool currentCentered=hasCentered && HasUiScaleTranslation() && HasScopedUiLifecycle() && HasUiScreenDimensions();
        return new Dictionary<string,object> {
            {"compatible",true}, {"version",module.Assembly.Name.Version.ToString()},
            {"widescreen",Number(wide)==1}, {"cap",cap},
            {"state",old.Count>0 ? "Razorbeam safe widescreen patch" : Number(wide)==1 ? "Existing widescreen patch" : "Vanilla"},
            {"centered",old.Count>0 && old["UiEnabled"]==1},
            {"ui_layout",currentCentered ? "centered-interface" : hasCentered ? "invalid-centered-interface" : "disabled"},
            {"settings",old},
            {"checks",hasCentered
                ? new[]{"Named widescreen assignment","Three matching graphics caps","Unique HiDef InitTargets cap","XNA profile limit","Isolated DrawInterface wrapper with finally restore","Terraria-owned UI SpriteBatch lifecycle preserved","UI logical dimensions follow selected display","Every UI zoom reset preserves selected-display coordinates","No window/menu/input-initialization replacement"}
                : new[]{"Named widescreen assignment","Three matching graphics caps","Unique HiDef InitTargets cap","XNA profile limit","No window/menu/input-initialization replacement"}}
        };
    }
    static void ExpandBranches(MethodDefinition m) {
        var ops=new Dictionary<Code,OpCode>{{Code.Br_S,OpCodes.Br},{Code.Brfalse_S,OpCodes.Brfalse},{Code.Brtrue_S,OpCodes.Brtrue},
            {Code.Beq_S,OpCodes.Beq},{Code.Bge_S,OpCodes.Bge},{Code.Bge_Un_S,OpCodes.Bge_Un},{Code.Bgt_S,OpCodes.Bgt},{Code.Bgt_Un_S,OpCodes.Bgt_Un},
            {Code.Ble_S,OpCodes.Ble},{Code.Ble_Un_S,OpCodes.Ble_Un},{Code.Blt_S,OpCodes.Blt},{Code.Blt_Un_S,OpCodes.Blt_Un},{Code.Bne_Un_S,OpCodes.Bne_Un},{Code.Leave_S,OpCodes.Leave}};
        foreach(var i in m.Body.Instructions) if(ops.ContainsKey(i.OpCode.Code)) i.OpCode=ops[i.OpCode.Code];
    }
    static FieldDefinition Setting(string key) { return settings.Fields.Single(f=>f.Name==key); }
    static void Load(ILProcessor il,string key) { il.Emit(OpCodes.Ldsfld,Setting(key)); }
    static void Insert(MethodDefinition m,Instruction before,params Instruction[] added) {
        // Incoming branches and handler boundaries must execute the inserted code too.
        foreach(var i in m.Body.Instructions.ToArray()) {
            if(i.Operand==before) i.Operand=added[0];
            var targets=i.Operand as Instruction[];
            if(targets!=null) for(int n=0;n<targets.Length;n++) if(targets[n]==before) targets[n]=added[0];
        }
        foreach(var h in m.Body.ExceptionHandlers) {
            if(h.TryStart==before)h.TryStart=added[0]; if(h.TryEnd==before)h.TryEnd=added[0];
            if(h.HandlerStart==before)h.HandlerStart=added[0]; if(h.HandlerEnd==before)h.HandlerEnd=added[0];
        }
        var il=m.Body.GetILProcessor(); foreach(var i in added) il.InsertBefore(before,i);
        ExpandBranches(m);
    }
    static VariableDefinition Local(MethodDefinition m,Instruction i) {
        if(i.OpCode==OpCodes.Ldloc_0)return m.Body.Variables[0];
        if(i.OpCode==OpCodes.Ldloc_1)return m.Body.Variables[1];
        if(i.OpCode==OpCodes.Ldloc_2)return m.Body.Variables[2];
        if(i.OpCode==OpCodes.Ldloc_3)return m.Body.Variables[3];
        Require(i.Operand is VariableDefinition,"Unknown resolution local");
        return (VariableDefinition)i.Operand;
    }
    static void InjectWindow() {
        var m=Method("LoadSettings");
        var flags=m.Body.Instructions.Single(i=>i.OpCode==OpCodes.Ldsfld && IsField(i,"screenBorderless") && i.Next.OpCode==OpCodes.Stsfld && IsField(i.Next,"PendingBorderlessState"));
        Insert(m,flags,Instruction.Create(OpCodes.Ldsfld,Setting("Mode")),Instruction.Create(OpCodes.Ldc_I4_2),Instruction.Create(OpCodes.Ceq),Instruction.Create(OpCodes.Stsfld,Field("startFullscreen")),
            Instruction.Create(OpCodes.Ldc_I4_0),Instruction.Create(OpCodes.Stsfld,Field("screenMaximized")),
            Instruction.Create(OpCodes.Ldc_I4_0),Instruction.Create(OpCodes.Stsfld,Field("screenBorderless")));
        var next=m.Body.Instructions.Single(i=>i.OpCode==OpCodes.Ldstr && (string)i.Operand=="DisplayHeight").Next.Next.Next;
        var w=Local(m,next); var h=Local(m,next.Next);
        Insert(m,next,Instruction.Create(OpCodes.Ldsfld,Setting("Width")),Instruction.Create(OpCodes.Stloc,w),
            Instruction.Create(OpCodes.Ldsfld,Setting("Height")),Instruction.Create(OpCodes.Stloc,h));
        var full=Method("FullscreenStartup");
        var fullNext=full.Body.Instructions.Single(i=>i.OpCode==OpCodes.Ldstr && (string)i.Operand=="DisplayHeight").Next.Next.Next;
        var fw=Local(full,fullNext);var fh=Local(full,fullNext.Next);
        Insert(full,fullNext,Instruction.Create(OpCodes.Ldsfld,Setting("Width")),Instruction.Create(OpCodes.Stloc,fw),
            Instruction.Create(OpCodes.Ldsfld,Setting("Height")),Instruction.Create(OpCodes.Stloc,fh));
        var point=Ref("System.Drawing.Point",".ctor",2);
        var location=Ref("System.Windows.Forms.Form","set_Location",1);
        var style=Ref("System.Windows.Forms.Form","set_FormBorderStyle",1);
        m=Method("SetBorderlessFormStyle"); m.Body=new MethodBody(m);
        var il=m.Body.GetILProcessor();
        il.Emit(OpCodes.Ldarg_0); Load(il,"WindowX"); Load(il,"WindowY"); il.Emit(OpCodes.Newobj,point); il.Emit(OpCodes.Callvirt,location);
        il.Emit(OpCodes.Ldarg_0); il.Emit(OpCodes.Ldc_I4_0); il.Emit(OpCodes.Callvirt,style); il.Emit(OpCodes.Ret);
        // Terraria's original borderless path forces a single monitor's bounds. Replace that narrow path.
        m=Method("SetDisplayModeAsBorderless"); m.Body=new MethodBody(m); il=m.Body.GetILProcessor();
        var done=Instruction.Create(OpCodes.Ret);
        il.Emit(OpCodes.Ldsfld,Field("screenBorderless")); il.Emit(OpCodes.Brfalse,done);
        il.Emit(OpCodes.Ldarg_0); Load(il,"Width"); il.Emit(OpCodes.Stind_I4);
        il.Emit(OpCodes.Ldarg_1); Load(il,"Height"); il.Emit(OpCodes.Stind_I4);
        il.Emit(OpCodes.Ldc_I4_0); il.Emit(OpCodes.Stsfld,Field("screenBorderlessPendingResizes"));
        il.Emit(OpCodes.Ldarg_2); il.Emit(OpCodes.Call,Method("SetBorderlessFormStyle")); il.Append(done);
        // Preserve odd dimensions instead of masking 1 to 0 (and then clamping to 800).
        var display=Method("SetDisplayMode");
        var masks=display.Body.Instructions.Where(i=>Number(i)==2147483646 && i.Next.OpCode==OpCodes.And).ToArray();
        Require(masks.Length==2,"SetDisplayMode: dimension masks changed");
        foreach(var mask in masks)SetInt(mask,Int32.MaxValue);
        // Position an ordinary window after settings have been loaded as well.
        m=Method("LoadSettings");
        var user32=module.ModuleReferences.SingleOrDefault(r=>String.Equals(r.Name,"user32.dll",StringComparison.OrdinalIgnoreCase));
        if(user32==null){user32=new ModuleReference("user32.dll");module.ModuleReferences.Add(user32);}
        var getWindowLong=new MethodDefinition("__Razorbeam_GetWindowLong",MethodAttributes.Private|MethodAttributes.Static|MethodAttributes.PInvokeImpl,module.TypeSystem.Int32);
        getWindowLong.Parameters.Add(new ParameterDefinition(module.TypeSystem.IntPtr));getWindowLong.Parameters.Add(new ParameterDefinition(module.TypeSystem.Int32));
        getWindowLong.PInvokeInfo=new PInvokeInfo(PInvokeAttributes.CallConvWinapi|PInvokeAttributes.CharSetUnicode|PInvokeAttributes.NoMangle,"GetWindowLongW",user32);getWindowLong.ImplAttributes=MethodImplAttributes.PreserveSig;main.Methods.Add(getWindowLong);
        var setWindowLong=new MethodDefinition("__Razorbeam_SetWindowLong",MethodAttributes.Private|MethodAttributes.Static|MethodAttributes.PInvokeImpl,module.TypeSystem.Int32);
        setWindowLong.Parameters.Add(new ParameterDefinition(module.TypeSystem.IntPtr));setWindowLong.Parameters.Add(new ParameterDefinition(module.TypeSystem.Int32));setWindowLong.Parameters.Add(new ParameterDefinition(module.TypeSystem.Int32));
        setWindowLong.PInvokeInfo=new PInvokeInfo(PInvokeAttributes.CallConvWinapi|PInvokeAttributes.CharSetUnicode|PInvokeAttributes.NoMangle,"SetWindowLongW",user32);setWindowLong.ImplAttributes=MethodImplAttributes.PreserveSig;main.Methods.Add(setWindowLong);
        var setWindowPos=new MethodDefinition("__Razorbeam_SetWindowPos",MethodAttributes.Private|MethodAttributes.Static|MethodAttributes.PInvokeImpl,module.TypeSystem.Boolean);
        foreach(var type in new[]{module.TypeSystem.IntPtr,module.TypeSystem.IntPtr,module.TypeSystem.Int32,module.TypeSystem.Int32,module.TypeSystem.Int32,module.TypeSystem.Int32,module.TypeSystem.UInt32})setWindowPos.Parameters.Add(new ParameterDefinition(type));
        setWindowPos.PInvokeInfo=new PInvokeInfo(PInvokeAttributes.CallConvWinapi|PInvokeAttributes.NoMangle,"SetWindowPos",user32);setWindowPos.ImplAttributes=MethodImplAttributes.PreserveSig;main.Methods.Add(setWindowPos);
        var isIconic=new MethodDefinition("__Razorbeam_IsIconic",MethodAttributes.Private|MethodAttributes.Static|MethodAttributes.PInvokeImpl,module.TypeSystem.Boolean);
        isIconic.Parameters.Add(new ParameterDefinition(module.TypeSystem.IntPtr));isIconic.PInvokeInfo=new PInvokeInfo(PInvokeAttributes.CallConvWinapi|PInvokeAttributes.NoMangle,"IsIconic",user32);isIconic.ImplAttributes=MethodImplAttributes.PreserveSig;main.Methods.Add(isIconic);
        var showWindow=new MethodDefinition("__Razorbeam_ShowWindow",MethodAttributes.Private|MethodAttributes.Static|MethodAttributes.PInvokeImpl,module.TypeSystem.Boolean);
        showWindow.Parameters.Add(new ParameterDefinition(module.TypeSystem.IntPtr));showWindow.Parameters.Add(new ParameterDefinition(module.TypeSystem.Int32));showWindow.PInvokeInfo=new PInvokeInfo(PInvokeAttributes.CallConvWinapi|PInvokeAttributes.NoMangle,"ShowWindow",user32);showWindow.ImplAttributes=MethodImplAttributes.PreserveSig;main.Methods.Add(showWindow);
        var place=new MethodDefinition("__Razorbeam_Position",MethodAttributes.Private|MethodAttributes.Static,module.TypeSystem.Void);
        main.Methods.Add(place);place.Body.InitLocals=true;var hwnd=new VariableDefinition(module.TypeSystem.IntPtr);var nativeStyle=new VariableDefinition(module.TypeSystem.Int32);place.Body.Variables.Add(hwnd);place.Body.Variables.Add(nativeStyle);il=place.Body.GetILProcessor();var end=Instruction.Create(OpCodes.Ret);
        Load(il,"Mode");il.Emit(OpCodes.Ldc_I4_2);il.Emit(OpCodes.Beq,end);
        var haveInstance=Instruction.Create(OpCodes.Callvirt,Ref("Microsoft.Xna.Framework.Game","get_Window",0));
        il.Emit(OpCodes.Ldsfld,Field("instance"));il.Emit(OpCodes.Dup);il.Emit(OpCodes.Brtrue,haveInstance);il.Emit(OpCodes.Pop);il.Emit(OpCodes.Br,end);il.Append(haveInstance);
        il.Emit(OpCodes.Callvirt,Ref("Microsoft.Xna.Framework.GameWindow","get_Handle",0));
        il.Emit(OpCodes.Stloc,hwnd);il.Emit(OpCodes.Ldloc,hwnd);il.Emit(OpCodes.Brfalse,end);
        var decorated=Instruction.Create(OpCodes.Ldloc,hwnd);var restoreIfMinimized=Instruction.Create(OpCodes.Nop);
        Load(il,"Mode");il.Emit(OpCodes.Ldc_I4_1);il.Emit(OpCodes.Bne_Un,decorated);
        // Exact former-helper Win32 path: WS_POPUP plus FRAMECHANGED, NOACTIVATE, SHOWWINDOW.
        il.Emit(OpCodes.Ldloc,hwnd);il.Emit(OpCodes.Ldc_I4,-16);il.Emit(OpCodes.Call,getWindowLong);il.Emit(OpCodes.Stloc,nativeStyle);
        il.Emit(OpCodes.Ldloc,hwnd);il.Emit(OpCodes.Ldc_I4,-16);il.Emit(OpCodes.Ldloc,nativeStyle);il.Emit(OpCodes.Ldc_I4,unchecked((int)~0x00CF0000));il.Emit(OpCodes.And);il.Emit(OpCodes.Ldc_I4,unchecked((int)0x80000000));il.Emit(OpCodes.Or);il.Emit(OpCodes.Call,setWindowLong);il.Emit(OpCodes.Pop);
        // Preserve the current Z-order. HWND_TOP without SWP_NOZORDER made the
        // game reclaim the foreground every 45 ticks.
        il.Emit(OpCodes.Ldloc,hwnd);il.Emit(OpCodes.Ldc_I4_0);il.Emit(OpCodes.Conv_I);Load(il,"WindowX");Load(il,"WindowY");Load(il,"Width");Load(il,"Height");il.Emit(OpCodes.Ldc_I4,0x74);il.Emit(OpCodes.Call,setWindowPos);il.Emit(OpCodes.Pop);il.Emit(OpCodes.Br,restoreIfMinimized);
        il.Append(decorated);il.Emit(OpCodes.Ldc_I4_0);il.Emit(OpCodes.Conv_I);Load(il,"WindowX");Load(il,"WindowY");il.Emit(OpCodes.Ldc_I4_0);il.Emit(OpCodes.Ldc_I4_0);il.Emit(OpCodes.Ldc_I4,0x35);il.Emit(OpCodes.Call,setWindowPos);il.Emit(OpCodes.Pop);
        il.Append(restoreIfMinimized);Load(il,"PreventMinimize");il.Emit(OpCodes.Brfalse,end);il.Emit(OpCodes.Ldloc,hwnd);il.Emit(OpCodes.Call,isIconic);il.Emit(OpCodes.Brfalse,end);il.Emit(OpCodes.Ldloc,hwnd);il.Emit(OpCodes.Ldc_I4_4);il.Emit(OpCodes.Call,showWindow);il.Emit(OpCodes.Pop);il.Append(end);
        Insert(m,m.Body.Instructions.Last(i=>i.OpCode==OpCodes.Ret),Instruction.Create(OpCodes.Call,place));
        // Steam launches do not have Razorbeam's external window helper. Reapply the
        // stored position after every display-mode transition inside Terraria itself.
        foreach(var ret in display.Body.Instructions.Where(i=>i.OpCode==OpCodes.Ret).ToArray())
            Insert(display,ret,Instruction.Create(OpCodes.Call,place));
        // Repeat at the same cadence as the former detached helper. This absorbs
        // late Steam/XNA window-style and bounds changes without touching input.
        var tick=new FieldDefinition("__Razorbeam_PositionTick",FieldAttributes.Private|FieldAttributes.Static,module.TypeSystem.Int32);main.Fields.Add(tick);
        var maintain=new MethodDefinition("__Razorbeam_MaintainWindow",MethodAttributes.Private|MethodAttributes.Static,module.TypeSystem.Void);main.Methods.Add(maintain);il=maintain.Body.GetILProcessor();var wait=Instruction.Create(OpCodes.Ret);
        il.Emit(OpCodes.Ldsfld,tick);il.Emit(OpCodes.Ldc_I4_1);il.Emit(OpCodes.Add);il.Emit(OpCodes.Dup);il.Emit(OpCodes.Stsfld,tick);
        il.Emit(OpCodes.Ldc_I4,45);il.Emit(OpCodes.Rem);il.Emit(OpCodes.Brtrue,wait);il.Emit(OpCodes.Call,place);il.Append(wait);
        var update=Method("DoUpdate");Insert(update,update.Body.Instructions[0],Instruction.Create(OpCodes.Call,maintain));
    }
    static void InjectTitle() {
        var filter=new MethodDefinition("__Razorbeam_Title",MethodAttributes.Private|MethodAttributes.Static,module.TypeSystem.String);
        filter.Parameters.Add(new ParameterDefinition(module.TypeSystem.String));main.Methods.Add(filter);
        var il=filter.Body.GetILProcessor();var keep=Instruction.Create(OpCodes.Ldarg_0);
        Load(il,"StableTitle");il.Emit(OpCodes.Brfalse,keep);il.Emit(OpCodes.Ldstr,"Terraria");il.Emit(OpCodes.Ret);il.Append(keep);il.Emit(OpCodes.Ret);
        var title=Method("SetTitle");
        foreach(var store in title.Body.Instructions.Where(i=>i.OpCode==OpCodes.Stfld && i.Operand is FieldReference && ((FieldReference)i.Operand).Name=="_cachedTitle").ToArray())
            Insert(title,store,Instruction.Create(OpCodes.Call,filter));
    }
    static void InjectApplyUiFields() {
        var apply=main.Methods.SingleOrDefault(m=>m.Name==ApplyUiFields);if(apply==null){apply=new MethodDefinition(ApplyUiFields,MethodAttributes.Private|MethodAttributes.Static|MethodAttributes.HideBySig,module.TypeSystem.Void);main.Methods.Add(apply);}
        apply.Body=new MethodBody(apply);var il=apply.Body.GetILProcessor();var names=new[]{"lastMouseX","lastMouseY","mouseX","mouseY"};
        for(int n=0;n<names.Length;n++){il.Emit(OpCodes.Ldsfld,Field(names[n]));Load(il,n%2==0?"UiX":"UiY");il.Emit(OpCodes.Conv_R4);il.Emit(OpCodes.Call,Method("get_UIScale"));il.Emit(OpCodes.Div);il.Emit(OpCodes.Conv_I4);il.Emit(OpCodes.Sub);il.Emit(OpCodes.Stsfld,Field(names[n]));}
        foreach(var pair in new[]{new[]{"UiWidth","screenWidth"},new[]{"UiHeight","screenHeight"}}){Load(il,pair[0]);il.Emit(OpCodes.Conv_R4);il.Emit(OpCodes.Call,Method("get_UIScale"));il.Emit(OpCodes.Div);il.Emit(OpCodes.Conv_I4);il.Emit(OpCodes.Stsfld,Field(pair[1]));}
        il.Emit(OpCodes.Ret);
    }
    static void InjectCenteredInterface() {
        const string name="DrawInterface";
        var original=Method(name);
        var wrapper=main.Methods.SingleOrDefault(m=>m.Name==CenteredInterface);
        bool created=wrapper==null;
        if(created) {
            wrapper=new MethodDefinition(CenteredInterface,MethodAttributes.Private|MethodAttributes.HideBySig,module.TypeSystem.Void);
            foreach(var p in original.Parameters) wrapper.Parameters.Add(new ParameterDefinition(p.Name,p.Attributes,p.ParameterType));
            main.Methods.Add(wrapper);
        }
        wrapper.Body=new MethodBody(wrapper);wrapper.Body.InitLocals=true;
        var il=wrapper.Body.GetILProcessor();
        var names=new[]{"lastMouseX","lastMouseY","mouseX","mouseY","screenWidth","screenHeight"};
        var saved=names.Select(n=>new VariableDefinition(module.TypeSystem.Int32)).ToArray();
        foreach(var v in saved)wrapper.Body.Variables.Add(v);
        var fallback=Instruction.Create(OpCodes.Ldarg_0);
        Load(il,"UiEnabled"); il.Emit(OpCodes.Brfalse,fallback);
        il.Emit(OpCodes.Ldsfld,Setting("InUi")); il.Emit(OpCodes.Brtrue,fallback);
        for(int n=0;n<names.Length;n++){il.Emit(OpCodes.Ldsfld,Field(names[n]));il.Emit(OpCodes.Stloc,saved[n]);}
        var start=Instruction.Create(OpCodes.Ldc_I4_1); il.Append(start); il.Emit(OpCodes.Stsfld,Setting("InUi"));
        il.Emit(OpCodes.Call,main.Methods.Single(m=>m.Name==ApplyUiFields));
        il.Emit(OpCodes.Ldarg_0); il.Emit(OpCodes.Ldarg_1); il.Emit(OpCodes.Call,original);
        var done=Instruction.Create(OpCodes.Ret); il.Emit(OpCodes.Leave,done);
        var finallyStart=Instruction.Create(OpCodes.Ldloc,saved[0]); il.Append(finallyStart); il.Emit(OpCodes.Stsfld,Field(names[0]));
        for(int n=1;n<names.Length;n++){il.Emit(OpCodes.Ldloc,saved[n]);il.Emit(OpCodes.Stsfld,Field(names[n]));}
        il.Emit(OpCodes.Ldc_I4_0);il.Emit(OpCodes.Stsfld,Setting("InUi"));
        il.Emit(OpCodes.Endfinally); il.Append(done);
        il.Append(fallback); il.Emit(OpCodes.Ldarg_1); il.Emit(OpCodes.Call,original); il.Emit(OpCodes.Ret);
        wrapper.Body.ExceptionHandlers.Add(new ExceptionHandler(ExceptionHandlerType.Finally){TryStart=start,TryEnd=finallyStart,HandlerStart=finallyStart,HandlerEnd=done});
        if(created) { var call=Calls(Method("DoDraw"),name).Single(); call.OpCode=OpCodes.Call;call.Operand=wrapper; }
    }
    static void InjectCenteredCraftingUpdate() {
        var crafting=module.Types.Single(t=>t.FullName=="Terraria.GameContent.UI.NewCraftingUI");
        var original=crafting.Methods.Single(m=>m.Name=="UpdateUI"&&m.IsStatic&&m.HasBody&&m.Parameters.Count==1);
        var caller=Method("UpdateUIStates");
        var site=Calls(caller,crafting.FullName,"UpdateUI").Single();
        var wrapper=new MethodDefinition(CenteredCraftingUpdate,MethodAttributes.Private|MethodAttributes.Static|MethodAttributes.HideBySig,module.TypeSystem.Void);
        wrapper.Parameters.Add(new ParameterDefinition(original.Parameters[0].Name,original.Parameters[0].Attributes,original.Parameters[0].ParameterType));
        main.Methods.Add(wrapper);wrapper.Body.InitLocals=true;
        var names=new[]{"lastMouseX","lastMouseY","mouseX","mouseY","screenWidth","screenHeight"};
        var saved=names.Select(n=>new VariableDefinition(module.TypeSystem.Int32)).ToArray();
        foreach(var v in saved)wrapper.Body.Variables.Add(v);
        var il=wrapper.Body.GetILProcessor();var fallback=Instruction.Create(OpCodes.Ldarg_0);
        Load(il,"UiEnabled");il.Emit(OpCodes.Brfalse,fallback);
        il.Emit(OpCodes.Ldsfld,Setting("InUi"));il.Emit(OpCodes.Brtrue,fallback);
        for(int n=0;n<names.Length;n++){il.Emit(OpCodes.Ldsfld,Field(names[n]));il.Emit(OpCodes.Stloc,saved[n]);}
        var start=Instruction.Create(OpCodes.Ldc_I4_1);il.Append(start);il.Emit(OpCodes.Stsfld,Setting("InUi"));
        il.Emit(OpCodes.Call,main.Methods.Single(m=>m.Name==ApplyUiFields));
        il.Emit(OpCodes.Ldarg_0);il.Emit(OpCodes.Call,original);
        var done=Instruction.Create(OpCodes.Ret);il.Emit(OpCodes.Leave,done);
        var finallyStart=Instruction.Create(OpCodes.Ldloc,saved[0]);il.Append(finallyStart);il.Emit(OpCodes.Stsfld,Field(names[0]));
        for(int n=1;n<names.Length;n++){il.Emit(OpCodes.Ldloc,saved[n]);il.Emit(OpCodes.Stsfld,Field(names[n]));}
        il.Emit(OpCodes.Ldc_I4_0);il.Emit(OpCodes.Stsfld,Setting("InUi"));il.Emit(OpCodes.Endfinally);il.Append(done);
        il.Append(fallback);il.Emit(OpCodes.Call,original);il.Emit(OpCodes.Ret);
        wrapper.Body.ExceptionHandlers.Add(new ExceptionHandler(ExceptionHandlerType.Finally){TryStart=start,TryEnd=finallyStart,HandlerStart=finallyStart,HandlerEnd=done});
        site.OpCode=OpCodes.Call;site.Operand=wrapper;ExpandBranches(wrapper);ExpandBranches(caller);
    }
    static bool HasUiScaleTranslation() {
        var m=Method("get_UIScaleMatrix");
        return m.Body.Instructions.Any(i=>i.OpCode==OpCodes.Ldsfld && i.Operand is FieldReference && ((FieldReference)i.Operand).FullName==Setting("InUi").FullName)
            && m.Body.Instructions.Any(i=>i.Operand is MethodReference && ((MethodReference)i.Operand).DeclaringType.FullName=="Microsoft.Xna.Framework.Matrix"
                && ((MethodReference)i.Operand).Name=="CreateTranslation" && ((MethodReference)i.Operand).Parameters.Count==3);
    }
    static bool HasScopedUiLifecycle() {
        var apply=main.Methods.Where(m=>m.Name==ApplyUiFields&&m.HasBody).ToArray();var bridge=main.Methods.Where(m=>m.Name==ScopedUiZoom&&m.HasBody).ToArray();
        var centered=main.Methods.Where(m=>m.Name==CenteredInterface&&m.HasBody).ToArray();if(apply.Length!=1||bridge.Length!=1||centered.Length!=1)return false;
        var original=PlayerInputZoom();
        if(Calls(bridge[0],original.DeclaringType.FullName,original.Name).Length!=1||Calls(bridge[0],ApplyUiFields).Length!=1||Calls(centered[0],ApplyUiFields).Length!=1)return false;
        if(centered[0].Body.ExceptionHandlers.Count!=1||centered[0].Body.ExceptionHandlers[0].HandlerType!=ExceptionHandlerType.Finally)return false;
        if(Calls(centered[0],"Microsoft.Xna.Framework.Graphics.SpriteBatch","Begin").Length!=0||Calls(centered[0],"Microsoft.Xna.Framework.Graphics.SpriteBatch","End").Length!=0)return false;
        if(module.Types.SelectMany(AllMethods).Where(m=>m.HasBody&&m!=bridge[0]).SelectMany(m=>Calls(m,original.DeclaringType.FullName,original.Name)).Any())return false;
        var layer=module.Types.SingleOrDefault(t=>t.FullName=="Terraria.UI.GameInterfaceLayer");if(layer==null)return false;
        var draw=layer.Methods.Where(m=>m.Name=="Draw"&&m.HasBody&&m.Parameters.Count==0).ToArray();if(draw.Length!=1||Calls(draw[0],ScopedUiZoom).Length!=1)return false;
        return true;
    }
    static void InjectFirstRenderError() {
        var logger=module.Types.Single(t=>t.FullName=="Terraria.TimeLogger").Methods.Single(m=>m.Name=="DrawException"&&m.HasBody);
        var seen=new FieldDefinition("__Razorbeam_RenderErrorLogged",FieldAttributes.Private|FieldAttributes.Static,module.TypeSystem.Boolean);logger.DeclaringType.Fields.Add(seen);
        var capture=new MethodDefinition("__Razorbeam_LogFirstRenderError",MethodAttributes.Private|MethodAttributes.Static,module.TypeSystem.Void);
        capture.Parameters.Add(new ParameterDefinition(module.ImportReference(typeof(Exception))));logger.DeclaringType.Methods.Add(capture);
        var il=capture.Body.GetILProcessor();var done=Instruction.Create(OpCodes.Ret);var start=Instruction.Create(OpCodes.Ldc_I4, (int)Environment.SpecialFolder.LocalApplicationData);
        il.Emit(OpCodes.Ldsfld,seen);il.Emit(OpCodes.Brtrue,done);il.Emit(OpCodes.Ldc_I4_1);il.Emit(OpCodes.Stsfld,seen);il.Append(start);
        il.Emit(OpCodes.Call,module.ImportReference(typeof(Environment).GetMethod("GetFolderPath",new[]{typeof(Environment.SpecialFolder)})));
        il.Emit(OpCodes.Ldstr,"Razorbeam-Terraria-first-render-error.txt");il.Emit(OpCodes.Call,module.ImportReference(typeof(Path).GetMethod("Combine",new[]{typeof(string),typeof(string)})));
        il.Emit(OpCodes.Ldarg_0);il.Emit(OpCodes.Callvirt,module.ImportReference(typeof(object).GetMethod("ToString")));
        il.Emit(OpCodes.Call,module.ImportReference(typeof(File).GetMethod("WriteAllText",new[]{typeof(string),typeof(string)})));il.Emit(OpCodes.Leave,done);
        var handler=Instruction.Create(OpCodes.Pop);il.Append(handler);il.Emit(OpCodes.Leave,done);il.Append(done);
        capture.Body.ExceptionHandlers.Add(new ExceptionHandler(ExceptionHandlerType.Catch){CatchType=module.ImportReference(typeof(Exception)),TryStart=start,TryEnd=handler,HandlerStart=handler,HandlerEnd=done});
        Insert(logger,logger.Body.Instructions[0],Instruction.Create(OpCodes.Ldarg_0),Instruction.Create(OpCodes.Call,capture));ExpandBranches(logger);
    }
    static void InjectNullSafeRockLayer() {
        // Multiplayer may leave distant tile entries null until their sections
        // arrive. A multi-monitor viewport reaches those entries before vanilla's
        // background renderer expects it. Treat missing tiles as inactive.
        var tile=module.Types.Single(t=>t.FullName=="Terraria.Tile");
        var active=tile.Methods.Single(m=>m.Name=="active"&&m.HasBody&&m.Parameters.Count==0&&m.ReturnType.FullName=="System.Boolean");
        var helper=new MethodDefinition("__Razorbeam_TileActive",MethodAttributes.Private|MethodAttributes.Static,module.TypeSystem.Boolean);
        helper.Parameters.Add(new ParameterDefinition(tile));main.Methods.Add(helper);
        var il=helper.Body.GetILProcessor();var haveTile=Instruction.Create(OpCodes.Ldarg_0);
        il.Emit(OpCodes.Ldarg_0);il.Emit(OpCodes.Brtrue,haveTile);il.Emit(OpCodes.Ldc_I4_0);il.Emit(OpCodes.Ret);
        il.Append(haveTile);il.Emit(OpCodes.Callvirt,active);il.Emit(OpCodes.Ret);
        var rock=main.Methods.Single(m=>m.Name=="DrawBackground_DrawRockLayer"&&m.HasBody);
        var calls=rock.Body.Instructions.Where(i=>i.OpCode==OpCodes.Callvirt&&i.Operand is MethodReference
            &&((MethodReference)i.Operand).DeclaringType.FullName==tile.FullName&&((MethodReference)i.Operand).Name=="active").ToArray();
        Require(calls.Length==9,"Rock background tile guards changed; expected nine active checks");
        foreach(var call in calls){call.OpCode=OpCodes.Call;call.Operand=helper;}
        ExpandBranches(rock);ExpandBranches(helper);
    }
    static void InjectUiClipBounds() {
        var clip=module.Types.Single(t=>t.FullName=="Terraria.UI.UIElement").Methods.Single(m=>m.Name=="GetClippingRectangle"&&m.HasBody);
        foreach(var axis in new[]{new[]{"screenWidth","Width"},new[]{"screenHeight","Height"}}) {
            var helper=new MethodDefinition("__Razorbeam_Clip"+axis[1],MethodAttributes.Assembly|MethodAttributes.Static,module.TypeSystem.Int32);
            helper.Parameters.Add(new ParameterDefinition(module.TypeSystem.Int32));main.Methods.Add(helper);
            var il=helper.Body.GetILProcessor();var fallback=Instruction.Create(OpCodes.Ldarg_0);
            il.Emit(OpCodes.Ldsfld,Setting("InUi"));il.Emit(OpCodes.Brfalse,fallback);
            Load(il,axis[1]);il.Emit(OpCodes.Ret);il.Append(fallback);il.Emit(OpCodes.Ret);
            var field=clip.Body.Instructions.Single(i=>i.OpCode==OpCodes.Ldsfld&&i.Operand is FieldReference&&((FieldReference)i.Operand).Name==axis[0]);
            var conversion=field.Next.Next.Next.Next;
            Require(conversion.OpCode==OpCodes.Conv_I4,"UI clipping bounds shape changed");
            Insert(clip,conversion.Next,Instruction.Create(OpCodes.Call,helper));
            ExpandBranches(clip);
        }
    }
    static void InjectUiScaleTranslation() {
        if(HasUiScaleTranslation())return;
        var m=Method("get_UIScaleMatrix");
        var returns=m.Body.Instructions.Where(i=>i.OpCode==OpCodes.Ret).ToArray();
        Require(returns.Length==1,"UIScaleMatrix getter shape changed");
        var ret=returns[0];
        Insert(m,ret,
            Instruction.Create(OpCodes.Ldsfld,Setting("InUi")),
            Instruction.Create(OpCodes.Brfalse,ret),
            Instruction.Create(OpCodes.Ldsfld,Setting("UiX")),
            Instruction.Create(OpCodes.Conv_R4),
            Instruction.Create(OpCodes.Ldsfld,Setting("UiY")),
            Instruction.Create(OpCodes.Conv_R4),
            Instruction.Create(OpCodes.Ldc_R4,0f),
            Instruction.Create(OpCodes.Call,Ref("Microsoft.Xna.Framework.Matrix","CreateTranslation",3)),
            Instruction.Create(OpCodes.Call,Ref("Microsoft.Xna.Framework.Matrix","op_Multiply",2)));
    }
    static TypeDefinition PlayerInputType() {
        var input=module.Types.SingleOrDefault(t=>t.FullName=="Terraria.GameInput.PlayerInput");Require(input!=null,"PlayerInput type missing");return input;
    }
    static MethodDefinition PlayerInputZoom() {
        var methods=PlayerInputType().Methods.Where(m=>m.Name=="SetZoom_UI"&&m.IsStatic&&m.HasBody&&m.Parameters.Count==0).ToArray();Require(methods.Length==1,"PlayerInput.SetZoom_UI shape changed");return methods[0];
    }
    static MethodDefinition PlayerInputGetter(string name) {
        var methods=PlayerInputType().Methods.Where(m=>m.Name==name&&m.IsStatic&&m.HasBody&&m.Parameters.Count==0&&m.ReturnType.FullName=="System.Int32").ToArray();
        Require(methods.Length==1,"PlayerInput."+name+" shape changed");return methods[0];
    }
    static bool HasUiScreenDimensions() {
        foreach(var pair in new[]{new[]{"get_UIScreenWidth","UiWidth"},new[]{"get_UIScreenHeight","UiHeight"}}) {
            var getter=PlayerInputGetter(pair[0]);
            if(!getter.Body.Instructions.Any(i=>i.OpCode==OpCodes.Ldsfld&&i.Operand is FieldReference&&((FieldReference)i.Operand).FullName==Setting("InUi").FullName))return false;
            if(!getter.Body.Instructions.Any(i=>i.OpCode==OpCodes.Ldsfld&&i.Operand is FieldReference&&((FieldReference)i.Operand).FullName==Setting(pair[1]).FullName))return false;
        }
        return true;
    }
    static void InjectUiScreenDimensions() {
        var input=PlayerInputType();
        foreach(var pair in new[]{new[]{"get_UIScreenWidth","_originalScreenWidth","UiWidth"},new[]{"get_UIScreenHeight","_originalScreenHeight","UiHeight"}}) {
            var getter=PlayerInputGetter(pair[0]);var original=input.Fields.SingleOrDefault(f=>f.Name==pair[1]&&f.IsStatic&&f.FieldType.FullName=="System.Int32");
            Require(original!=null,"PlayerInput."+pair[1]+" shape changed");
            getter.Body=new MethodBody(getter);var il=getter.Body.GetILProcessor();var fallback=Instruction.Create(OpCodes.Ldsfld,original);
            il.Emit(OpCodes.Ldsfld,Setting("InUi"));il.Emit(OpCodes.Brfalse,fallback);
            Load(il,pair[2]);il.Emit(OpCodes.Conv_R4);il.Emit(OpCodes.Call,Method("get_UIScale"));il.Emit(OpCodes.Div);il.Emit(OpCodes.Conv_I4);il.Emit(OpCodes.Ret);
            il.Append(fallback);il.Emit(OpCodes.Conv_R4);il.Emit(OpCodes.Call,Method("get_UIScale"));il.Emit(OpCodes.Div);il.Emit(OpCodes.Conv_I4);il.Emit(OpCodes.Ret);
        }
    }
    static void InjectScopedUiZoom() {
        var original=PlayerInputZoom();var bridge=main.Methods.SingleOrDefault(m=>m.Name==ScopedUiZoom);
        if(bridge==null){bridge=new MethodDefinition(ScopedUiZoom,MethodAttributes.Assembly|MethodAttributes.Static|MethodAttributes.HideBySig,module.TypeSystem.Void);main.Methods.Add(bridge);}
        bridge.Body=new MethodBody(bridge);var il=bridge.Body.GetILProcessor();var done=Instruction.Create(OpCodes.Ret);
        il.Emit(OpCodes.Call,original);il.Emit(OpCodes.Ldsfld,Setting("InUi"));il.Emit(OpCodes.Brfalse,done);il.Emit(OpCodes.Call,main.Methods.Single(m=>m.Name==ApplyUiFields));il.Append(done);
        var sites=module.Types.SelectMany(AllMethods).Where(m=>m.HasBody&&m!=bridge)
            .SelectMany(m=>Calls(m,original.DeclaringType.FullName,original.Name)).ToArray();
        Require(sites.Length>0,"No PlayerInput.SetZoom_UI call sites found");
        foreach(var site in sites){site.OpCode=OpCodes.Call;site.Operand=bridge;}
    }
    static void InjectSplash() {
        var original=Method("DrawSplash");
        var wrapper=new MethodDefinition("__Razorbeam_DrawSplash",MethodAttributes.Private|MethodAttributes.HideBySig,module.TypeSystem.Void);
        foreach(var p in original.Parameters)wrapper.Parameters.Add(new ParameterDefinition(p.Name,p.Attributes,p.ParameterType));
        main.Methods.Add(wrapper);wrapper.Body.InitLocals=true;var il=wrapper.Body.GetILProcessor();
        var getDevice=Ref("Microsoft.Xna.Framework.Game","get_GraphicsDevice",0);
        var getViewport=Ref("Microsoft.Xna.Framework.Graphics.GraphicsDevice","get_Viewport",0);
        var setViewport=new MethodReference("set_Viewport",module.TypeSystem.Void,getViewport.DeclaringType){HasThis=true};
        setViewport.Parameters.Add(new ParameterDefinition(getViewport.ReturnType));
        var vpCtor=new MethodReference(".ctor",module.TypeSystem.Void,getViewport.ReturnType){HasThis=true};
        for(int n=0;n<4;n++)vpCtor.Parameters.Add(new ParameterDefinition(module.TypeSystem.Int32));
        var device=new VariableDefinition(getDevice.ReturnType);wrapper.Body.Variables.Add(device);
        var viewport=new VariableDefinition(getViewport.ReturnType);wrapper.Body.Variables.Add(viewport);
        var fallback=Instruction.Create(OpCodes.Ldarg_0);Load(il,"SplashEnabled");il.Emit(OpCodes.Brfalse,fallback);
        il.Emit(OpCodes.Ldarg_0);il.Emit(OpCodes.Call,getDevice);il.Emit(OpCodes.Stloc,device);
        il.Emit(OpCodes.Ldloc,device);il.Emit(OpCodes.Callvirt,getViewport);il.Emit(OpCodes.Stloc,viewport);
        var start=Instruction.Create(OpCodes.Ldloc,device);il.Append(start);
        foreach(var key in new[]{"SplashX","SplashY","SplashWidth","SplashHeight"})Load(il,key);
        il.Emit(OpCodes.Newobj,vpCtor);il.Emit(OpCodes.Callvirt,setViewport);
        il.Emit(OpCodes.Ldarg_0);il.Emit(OpCodes.Ldarg_1);il.Emit(OpCodes.Call,original);
        var done=Instruction.Create(OpCodes.Ret);il.Emit(OpCodes.Leave,done);
        var finallyStart=Instruction.Create(OpCodes.Ldloc,device);il.Append(finallyStart);il.Emit(OpCodes.Ldloc,viewport);il.Emit(OpCodes.Callvirt,setViewport);il.Emit(OpCodes.Endfinally);il.Append(done);
        il.Append(fallback);il.Emit(OpCodes.Ldarg_1);il.Emit(OpCodes.Call,original);il.Emit(OpCodes.Ret);
        wrapper.Body.ExceptionHandlers.Add(new ExceptionHandler(ExceptionHandlerType.Finally){TryStart=start,TryEnd=finallyStart,HandlerStart=finallyStart,HandlerEnd=done});
        var call=Calls(Method("DoDraw"),"DrawSplash").Single();call.OpCode=OpCodes.Call;call.Operand=wrapper;
    }
    static void SetSettings(Dictionary<string,int> values) {
        if(settings==null) {
            settings=new TypeDefinition("Razorbeam","TerrariaPatch",TypeAttributes.NotPublic|TypeAttributes.Abstract|TypeAttributes.Sealed,module.TypeSystem.Object);
            module.Types.Add(settings);
            foreach(var key in Keys)settings.Fields.Add(new FieldDefinition(key,FieldAttributes.Public|FieldAttributes.Static,module.TypeSystem.Int32));
            settings.Fields.Add(new FieldDefinition("InUi",FieldAttributes.Public|FieldAttributes.Static,module.TypeSystem.Int32));
            settings.Methods.Add(new MethodDefinition(".cctor",MethodAttributes.Private|MethodAttributes.Static|MethodAttributes.SpecialName|MethodAttributes.RTSpecialName,module.TypeSystem.Void));
        }
        var m=settings.Methods.Single(x=>x.Name==".cctor");m.Body=new MethodBody(m);var il=m.Body.GetILProcessor();
        foreach(var key in Keys){il.Emit(OpCodes.Ldc_I4,values[key]);il.Emit(OpCodes.Stsfld,Setting(key));} il.Emit(OpCodes.Ret);
    }
    static void Open(string path) {
        module=ModuleDefinition.ReadModule(path,new ReaderParameters{InMemory=true,ReadSymbols=false});
        main=module.Types.SingleOrDefault(x=>x.FullName=="Terraria.Main"); Require(main!=null,"Terraria.Main not found");
        settings=module.Types.SingleOrDefault(x=>x.FullName==Tag);
    }
    static int Main(string[] args) {
        try {
            Require(args.Length>=2,"Usage: analyze <exe> | patch <exe> <staged-exe> <settings.json>");
            Open(args[1]);var report=Analyze();
            if(args[0]=="patch") {
                Require(args.Length==4,"Patch requires staging path and settings JSON");
                Require(!String.Equals(Path.GetFullPath(args[1]),Path.GetFullPath(args[2]),StringComparison.OrdinalIgnoreCase),"Refusing to overwrite input");
                Require(!File.Exists(args[2]),"Staging path already exists");
                var rawValues=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[3]));
                var values=new Dictionary<string,int>();
                foreach(var key in Keys){Require(rawValues.ContainsKey(key),"Missing setting "+key);values[key]=Convert.ToInt32(rawValues[key]);}
                Require(values["Schema"]==3 && values["Width"]>0 && values["Height"]>0,"Invalid dimensions/schema");
                Require(values["UiEnabled"]==0||values["UiEnabled"]==1,"Invalid centered UI toggle");
                Require(values["Mode"]>=0 && values["Mode"]<=2 && (values["StableTitle"]==0||values["StableTitle"]==1),"Invalid window mode/title setting");
                Require(values["SplashEnabled"]==0||values["SplashEnabled"]==1,"Invalid splash centering toggle");
                Require(values["PreventMinimize"]==0||values["PreventMinimize"]==1,"Invalid minimized-window setting");
                Require(values["SplashX"]>=0&&values["SplashY"]>=0&&values["SplashWidth"]>0&&values["SplashHeight"]>0
                    &&(long)values["SplashX"]+values["SplashWidth"]<=values["Width"]&&(long)values["SplashY"]+values["SplashHeight"]<=values["Height"],"Splash viewport is outside render surface");
                Require(values["Cap"]>=Math.Max(8192,Math.Max(values["Width"],values["Height"])),"Cap is below requested resolution");
                Require(values["UiX"]>=0 && values["UiY"]>=0 && values["UiWidth"]>0 && values["UiHeight"]>0
                    && (long)values["UiX"]+values["UiWidth"]<=values["Width"] && (long)values["UiY"]+values["UiHeight"]<=values["Height"],"UI rectangle is outside render surface");
                bool first=settings==null;int previous=(int)report["cap"];
                SetInt(StoreValue(Method(".cctor"),"SupportWideScreen",x=>x==0||x==1),1);
                foreach(var f in new[]{"maxScreenW","maxScreenH","_renderTargetMaxSize"})SetInt(StoreValue(Method("SetGraphicsProfileInternal"),f,x=>x==previous),values["Cap"]);
                SetInt(InitCap(previous),values["Cap"]);SetInt(FrameworkCap(first ? 8192 : previous),values["Cap"]);
                SetSettings(values);
                if(first) {
                    InjectWindow();
                    InjectTitle();
                    InjectSplash();
                    InjectFirstRenderError();
                    InjectNullSafeRockLayer();
                }
                if(values["UiEnabled"]==1 || main.Methods.Any(m=>m.Name==CenteredInterface)) {
                    InjectApplyUiFields();
                    InjectScopedUiZoom();
                    InjectUiScreenDimensions();
                    InjectCenteredInterface();
                    InjectCenteredCraftingUpdate();
                    InjectUiScaleTranslation();
                    InjectUiClipBounds();
                }
                // Changing ldc.i4.0 to ldc.i4 grows the main static initializer too.
                foreach(var m in main.Methods.Where(x=>x.HasBody))ExpandBranches(m);
                var references=module.AssemblyReferences.Select(a=>a.FullName).ToArray();
                module.Write(args[2]);module.Dispose();Open(args[2]);report=Analyze();
                Require(module.AssemblyReferences.Select(a=>a.FullName).SequenceEqual(references),"Unexpected runtime dependency introduced");
                var verified=ReadSettings();foreach(var key in Keys)Require(verified[key]==values[key],"Setting verification failed: "+key);
                Require((bool)report["widescreen"] && (int)report["cap"]==values["Cap"] && (bool)report["centered"]==(values["UiEnabled"]==1),"Patch verification failed");
                Require(main.Methods.Count(m=>m.Name=="__Razorbeam_Position"&&m.HasBody)==1
                    && Calls(Method("SetDisplayMode"),"__Razorbeam_Position").Length>0,"Persistent Steam-launch window positioning verification failed");
                Require(main.Methods.Count(m=>m.Name=="__Razorbeam_MaintainWindow"&&m.HasBody)==1
                    && Calls(Method("DoUpdate"),"__Razorbeam_MaintainWindow").Length==1
                    && Calls(Method("__Razorbeam_MaintainWindow"),"__Razorbeam_Position").Length==1,
                    "Persistent Steam-launch window maintenance verification failed");
                var position=Method("__Razorbeam_Position");
                var positionFlags=position.Body.Instructions.Where(i=>i.Operand is MethodReference
                    &&((MethodReference)i.Operand).Name=="__Razorbeam_SetWindowPos").Select(i=>Number(i.Previous)).ToArray();
                Require(positionFlags.Length==2 && positionFlags.Any(v=>v==0x74) && positionFlags.Any(v=>v==0x35),
                    "Window positioning must preserve Z-order and focus");
                Require(Calls(position,"__Razorbeam_ShowWindow").Length==1,"Minimized-window recovery verification failed");
                Require(position.Body.Instructions.Count(i=>i.OpCode==OpCodes.Ldsfld&&i.Operand is FieldReference&&((FieldReference)i.Operand).FullName==Setting("PreventMinimize").FullName)==1,
                    "Minimized-window preference is not wired into window maintenance");
                Require(main.Methods.Count(m=>m.Name=="__Razorbeam_TileActive"&&m.HasBody)==1
                    && Calls(Method("DrawBackground_DrawRockLayer"),"__Razorbeam_TileActive").Length==9,
                    "Multiplayer rock-background null guard verification failed");
                Require(main.Methods.All(m=>m.Name!=CenteredInterface) || HasUiScaleTranslation() && HasScopedUiLifecycle() && HasUiScreenDimensions(),
                    "Centered UI scoped-lifecycle verification failed");
                Require(values["UiEnabled"]==0 || main.Methods.Count(m=>m.Name==CenteredCraftingUpdate&&m.HasBody)==1
                    && Calls(Method("UpdateUIStates"),CenteredCraftingUpdate).Length==1,
                    "Centered crafting input-update verification failed");
                report["verified"]=true;
            } else Require(args[0]=="analyze","Unknown command");
            Console.WriteLine(Json.Serialize(report));return 0;
        } catch(Exception e) { Console.WriteLine(Json.Serialize(new {compatible=false,error=e.Message}));return 1; }
        finally { if(module!=null)module.Dispose(); }
    }
}
