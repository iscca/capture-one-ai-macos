    function idNumber(text) {
        let value = 2166136261;
        for (let i=0;i<text.length;i++) value = Math.imul(value ^ text.charCodeAt(i),16777619);
        return value >>> 0;
    }
    function layerRows() {
        const v=a.primaryVariant(), names=v.layers.name(), rows=[];
        if (new Set(names).size !== names.length) fail('AMBIGUOUS_LAYERS','Duplicate native layer names cannot be targeted safely');
        for (let i=0;i<names.length;i++) {
            const l=v.layers.at(i), base=l.kind()==='background';
            const marker=names[i].match(/ \[c1ai:([a-f0-9]+)\]$/);
            const id=base?'background':marker?marker[1]:'existing:'+names[i];
            rows.push({runtime_id:[idNumber(id)],native_id:id,object_id:id,
                name:marker?names[i].slice(0,marker.index):names[i],is_image_layer:base});
        }
        return rows;
    }
    function selectedLayerRef() {
        const name=a.primaryVariant().currentLayer().name(), names=a.primaryVariant().layers.name();
        const rows=layerRows(), index=names.indexOf(name);
        if(index<0) fail('LAYER_CHANGED','Current layer disappeared');
        return rows[index];
    }
    function layerFor(ref) {
        const rows=layerRows(), index=rows.findIndex(r=>r.native_id===ref.native_id);
        if(index<0 || stable(rows[index])!==stable(ref)) fail('LAYER_CHANGED','Layer reference changed');
        return a.primaryVariant().layers.at(index);
    }
    function settings() {return a.primaryVariant().currentLayer().adjustments();}
    function scalarValue(field){
        const value=settings()[q.properties[field]]();
        const neutral=['exposure','contrast','brightness','saturation','highlight_recovery','shadow_recovery','white_recovery','black_recovery'];
        if(value===null && a.primaryVariant().currentLayer().kind()!=='background' && neutral.includes(field))return 0;
        if(typeof value!=='number')fail('NATIVE_VALUE_UNAVAILABLE','Native scalar is not numeric: '+field);
        return value;
    }

    const channels=['rgb','red','green','blue'], curveChannels=channels.concat(['luma']);
    const levelProps={input_black:'levelShadow',input_white:'levelHighlight',midtone:'levelMidtone',
                      output_black:'levelTargetShadow',output_white:'levelTargetHighlight'};
    function suffix(c){return c[0].toUpperCase()+c.slice(1);}
    function rawLevels(s=settings()){
        let out={};
        for(const c of channels){out[c]={};for(const k of Object.keys(levelProps))out[c][k]=s[levelProps[k]+suffix(c)]();}
        return out;
    }
    function rawCurves(s=settings()){
        const out={};
        for(const c of curveChannels){const curve=s[c+'Curve']();out[c]=[];
            const count=curve.curvePoints().length;
            for(let i=0;i<count;i++){const p=curve.curvePoints.at(i);out[c].push([p.brightness()/100,p.amount()/100]);}
        }return out;
    }
    function rawMixer(s=settings()){
        const ce=s.colorEditorSettings();
        const names=ce.basicColorCorrections.name(),h=ce.basicColorCorrections.hueChange(),
              sat=ce.basicColorCorrections.saturationChange(),light=ce.basicColorCorrections.lightnessChange();
        let colors={};for(let i=0;i<names.length;i++)colors[names[i]==='pink'?'magenta':names[i]]={hue:h[i],saturation:sat[i],lightness:light[i]};
        const fields=['enabled','red','green','blue','hueStart','hueEnd','saturationStart','saturationEnd','smoothness','hueChange','saturationChange','lightnessChange'];
        let advanced=[],count=ce.advancedColorCorrections().length;
        for(let i=0;i<count;i++){const p=ce.advancedColorCorrections.at(i),row={};for(const f of fields)row[f]=p[f]();advanced.push(row);}
        return {mode:'basic',colors:colors,advanced:advanced,skin:{available:false,reason:'Not exposed by installed scripting dictionary'}};
    }
    function rawBalance(s=settings()){
        const out={};
        for(const w of ['master','shadow','midtone','highlight']){
            const p='colorBalance'+suffix(w),h=s[p+'Hue'](),sat=s[p+'Saturation']();
            out[w+'_x']=Math.cos(h*Math.PI/180)*sat*50;out[w+'_y']=Math.sin(h*Math.PI/180)*sat*50;
            if(w!=='master')out[w+'_lightness']=s[p+'Lightness']()*50;
        }return out;
    }
    function revisionState(){
        const v=a.primaryVariant(),rows=[];
        for(let i=0;i<v.layers().length;i++){
            const l=v.layers.at(i),s=l.adjustments(),values={};
            for(const f of Object.keys(q.properties))values[f]=s[q.properties[f]]();
            let lr=null;
            if(l.kind()!=='background'){const r=l.lumaRange();lr={};for(const key of ['rangeLow','rangeHigh','falloffLow','falloffHigh','invert','radius','sensitivity'])lr[key]=r[key]();}
            rows.push({name:l.name(),kind:l.kind(),enabled:l.enabled(),opacity:l.opacity(),values:values,
                curves:rawCurves(s),levels:rawLevels(s),mixer:rawMixer(s),balance:rawBalance(s),luma:lr});
        }
        return stable({background:v.adjustments().properties(),layers:rows});
    }
    function rawGrain(){
        if(a.primaryVariant().currentLayer().kind()!=='background')fail('BACKGROUND_REQUIRED','Film grain is a global adjustment');
        const s=settings();return {type:s.filmGrainType().replace(/ /g,'_'),impact:s.filmGrainImpact(),granularity:s.filmGrainGranularity()};
    }
    function setGrain(p){
        const s=settings();
        for(const key of ['type','impact','granularity'])if(key in p){
            mutated=true;
            if(key==='type')s.filmGrainType=p.type.replace(/_/g,' ');
            else s[key==='impact'?'filmGrainImpact':'filmGrainGranularity']=p[key];
        }
    }
    function rawMask(){
        const l=a.primaryVariant().currentLayer(),base=l.kind()==='background';
        let luma=null;
        if(!base){const lr=l.lumaRange();luma={};for(const k of ['rangeLow','rangeHigh','falloffLow','falloffHigh','invert','radius','sensitivity'])luma[k]=lr[k]();}
        return {applicable:!base,luma:luma,pixels_readable:false,submasks_readable:false};
    }
    function structured(module){
        const obs=snapshot([]);let state;
        if(module==='film_grain')state=rawGrain();
        else if(module==='curves')state={channels:rawCurves()};
        else if(module==='levels')state={channels:rawLevels()};
        else if(module==='color_mixer')state=rawMixer();
        else if(module==='color_balance')state={values:rawBalance()};
        else if(module==='layers'||module==='masks'){
            const ref=selectedLayerRef(),l=a.primaryVariant().currentLayer();
            state={layers:layerRows(),selected_layer:ref,opacity:l.opacity(),enabled:l.enabled()};
            if(module==='masks')state.mask=rawMask();
        }else fail('UNSUPPORTED','Unknown structured module');
        guardReadTarget(obs.target);
        return {...obs,module:module,state:state};
    }
    function guardReadTarget(target){if(stable(identity())!==stable(target))fail('TARGET_CHANGED','Native target changed during module read');}
    function closeEnough(a,b,tol){
        if(typeof a==='number'&&typeof b==='number')return Math.abs(a-b)<=(tol||0.0001);
        if(a===null||b===null||typeof a!=='object'||typeof b!=='object')return a===b;
        const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>k in b&&closeEnough(a[k],b[k],tol));
    }
    function setLevelChannel(channel,values){
        const s=settings(),p=suffix(channel), current=rawLevels()[channel];
        for(const pair of [['input_black','input_white'],['output_black','output_white']]){
            const low=pair[0],high=pair[1];
            if(values[low]>=values[high])fail('INVALID_LEVELS','Black point must remain below white point');
            const order=values[low]>=current[high]?[high,low]:[low,high];
            for(const k of order){mutated=true;s[levelProps[k]+p]=values[k];}
        }mutated=true;s[levelProps.midtone+p]=values.midtone;
    }
    function nativeCommands(commands){
        const v=a.primaryVariant();
        const lines=['tell application "/Applications/Capture One.app"',
            'if (id of primary variant as text) is not '+JSON.stringify(String(v.id()))+' then error "Variant changed"',
            'if (id of current document as text) is not '+JSON.stringify(String(a.currentDocument().id()))+' then error "Document changed"',
            'if (name of current layer of primary variant as text) is not '+JSON.stringify(v.currentLayer().name())+' then error "Layer changed"'];
        lines.push(...commands,'end tell','return "ok"');
        const source=$.NSAppleScript.alloc.initWithSource(lines.join('\n'));
        const error=Ref();mutated=true;const result=source.executeAndReturnError(error);
        if(!result)fail('NATIVE_COMMAND_FAILED',String(ObjC.deepUnwrap(error[0])));
    }
    function setCurve(channel,points){
        const v=a.primaryVariant(),name=v.currentLayer().name();
        const quoted=JSON.stringify;
        let body=['tell application "/Applications/Capture One.app"',
            'if (id of primary variant as text) is not '+quoted(String(v.id()))+' then error "Variant changed"',
            'if (id of current document as text) is not '+quoted(String(a.currentDocument().id()))+' then error "Document changed"',
            'if (name of current layer of primary variant as text) is not '+quoted(name)+' then error "Layer changed"',
            'tell '+channel+' curve of adjustments of current layer of primary variant',
            'repeat while (count of curve points) > 2', 'delete curve point 2','end repeat',
            'set amount of first curve point to '+String(points[0][1]*100),
            'set amount of last curve point to '+String(points[points.length-1][1]*100)];
        for(let i=1;i<points.length-1;i++)body.push('make new curve point with properties {brightness:'+String(points[i][0]*100)+', amount:'+String(points[i][1]*100)+'}');
        body.push('end tell','end tell','return "ok"');
        const script=$.NSAppleScript.alloc.initWithSource(body.join('\n'));
        const error=Ref();mutated=true;
        const result=script.executeAndReturnError(error);
        if(!result)fail('CURVE_APPLESCRIPT_FAILED',String(ObjC.deepUnwrap(error[0])));
    }
    function setBasic(color,patch){
        const ce=settings().colorEditorSettings(),name=color==='magenta'?'pink':color;
        const names=ce.basicColorCorrections.name(),i=names.indexOf(name);
        if(i<0)fail('UNSUPPORTED_COLOR','Native Basic color sector not found');
        const p=ce.basicColorCorrections.at(i);
        for(const k of ['hue','saturation','lightness'])if(k in patch){mutated=true;p[k+'Change']=patch[k];}
    }
    function setBalance(wheel,patch){
        const names={master:'master',shadows:'shadow',midtones:'midtone',highlights:'highlight'},w=names[wheel],s=settings();
        const before=rawBalance(),x=('x' in patch)?patch.x:before[w+'_x'],y=('y' in patch)?patch.y:before[w+'_y'];
        const p='colorBalance'+suffix(w);
        if('x' in patch||'y' in patch){mutated=true;s[p+'Hue']=(Math.atan2(y,x)*180/Math.PI+360)%360;s[p+'Saturation']=Math.hypot(x,y)/50;}
        if('lightness' in patch){mutated=true;s[p+'Lightness']=patch.lightness/50;}
    }
    function rawInventory(scope){
        const env=environment();if(!env.document)fail('DOCUMENT_REQUIRED','Open a document');
        const d=a.currentDocument(),ids=d.variants.id(),names=d.variants.name(),visible=d.variants.visible(),
              selected=a.selectedVariants().map(v=>String(v.id()));
        let items=[];for(let i=0;i<ids.length;i++)if(scope==='all'||visible[i])items.push({variant_uuid:String(ids[i]),name:names[i],selected:selected.map(String).includes(String(ids[i]))});
        return {instance:env.instance,document:env.document,collection_id:String(d.currentCollection().id()),items:items,selected_ids:selected.map(String)};
    }
    function toolRead(){return structured(q.module);}
    function toolAdjust(){
        const before=structured(q.module);
        if(stable(before.state)!==stable(q.before_state))fail('STALE_MODULE','Module or layer state changed before dispatch');
        guard();const p=q.patch,module=q.module;
        if(module==='film_grain')setGrain(p);
        else if(module==='levels')setLevelChannel(p.channel,{...before.state.channels[p.channel],...Object.fromEntries(Object.entries(p).filter(([k])=>k!=='channel'))});
        else if(module==='curves')setCurve(p.channel,p.points);
        else if(module==='color_balance')setBalance(p.wheel,p);
        else if(module==='color_mixer'){
            if(p.mode==='basic')setBasic(p.color,p);
            else if(p.mode==='advanced'){
                const ce=settings().colorEditorSettings();let i=p.index;
                if(p.action==='add'){i=ce.advancedColorCorrections().length;mutated=true;nativeCommands(['make new advanced color correction at color editor settings of adjustments of current layer of primary variant']);}
                if(i<0||i>=ce.advancedColorCorrections().length)fail('STALE_INDEX','Native range no longer exists');
                const row=ce.advancedColorCorrections.at(i);
                if(p.action==='remove'){mutated=true;a.delete(row);}
                else{
                    if(p.rgb){for(let j=0;j<3;j++){mutated=true;row[['red','green','blue'][j]]=p.rgb[j];}}
                    if(p.action==='add'){const hi=Math.max(...p.rgb),lo=Math.min(...p.rgb),sat=hi===0?0:(hi-lo)/hi*100;row.hueStart=-15;row.hueEnd=15;row.saturationStart=-Math.min(30,sat);row.saturationEnd=Math.min(30,100-sat);}
                    const map={enabled:'enabled',hue:'hueChange',saturation:'saturationChange',lightness:'lightnessChange',smoothness:'smoothness',hue_low:'hueStart',hue_high:'hueEnd',saturation_low:'saturationStart',saturation_high:'saturationEnd'};
                    for(const k of Object.keys(map))if(k in p){mutated=true;row[map[k]]=p[k];}
                }
            }else fail('SKIN_UNAVAILABLE','Skin Tone properties are absent from the scripting interface');
        }else if(module==='layers'||module==='masks'){
            const v=a.primaryVariant(),action=p.action;
            if(['create_empty','create_subject','create_background'].includes(action)){
                const kind={create_empty:'adjustment',create_subject:'subject mask',create_background:'background mask'}[action];
                const name=q.new_name+' [c1ai:'+q.new_id+']';
                mutated=true;const l=a.make({new:'layer',at:v,withProperties:{name:name,kind:kind}});v.currentLayer=l;
            }else if(module==='layers'){
                const l=layerFor(p.layer_ref);
                if(action==='select'){mutated=true;v.currentLayer=l;}
                else if(action==='set_opacity'){mutated=true;l.opacity=p.opacity;}
                else if(action==='set_enabled'){mutated=true;l.enabled=p.enabled;}
                else if(action==='rename'){
                    if(p.layer_ref.native_id.startsWith('existing:'))fail('UNSTABLE_LAYER_ID','Rename is limited to tool-created tagged layers');
                    mutated=true;l.name=p.name+' [c1ai:'+p.layer_ref.native_id+']';
                }else if(action==='remove_created'){mutated=true;a.delete(l);v.currentLayer=v.layers.at(0);}
                else if(action==='move'){
                    const rows=layerRows(),idx=p.index+1;if(idx<1||idx>=rows.length)fail('INVALID_INDEX','Layer destination outside adjustment layers');
                    const from=rows.findIndex(r=>r.native_id===p.layer_ref.native_id);
                    if(from!==idx)nativeCommands(['move current layer of primary variant to '+(from<idx?'after':'before')+' layer '+String(idx+1)+' of primary variant']);
                }else if(action==='duplicate'){
                    const name=(p.name||p.layer_ref.name+' copy')+' [c1ai:'+q.new_id+']';
                    if(typeof a.duplicate!=='function')fail('LAYER_DUPLICATE_NATIVE_MISSING','This host exposes no native duplicate-layer command');
                    mutated=true;const copy=a.duplicate(l,{to:v});copy.name=name;v.currentLayer=copy;
                }else fail('UNSUPPORTED_LAYER_ACTION','Layer action has no native implementation');
            }else{
                const l=v.currentLayer();if(l.kind()==='background')fail('ADJUSTMENT_LAYER_REQUIRED','Select an adjustment layer');
                if(action==='clear'){mutated=true;a.clearMask(l);}
                else if(action==='invert'){mutated=true;a.invertMask(l);}
                else if(action==='fill'){if(p.density!==1)fail('MASK_DENSITY_UNAVAILABLE','Native fill supports only density=1');mutated=true;a.fillMask(l);}
                else if(action==='feather'){if(!('native_amount' in p))fail('MASK_UNITS_UNAVAILABLE','Specify native_amount (0..100), not an unconverted pixel radius');mutated=true;a.featherMask(l,{amount:p.native_amount});}
                else if(action==='copy'){const source=layerFor(p.source_layer);mutated=true;a.copyMask(source,{toLayer:l});}
                else if(action==='luma_range'){
                    const lr=l.lumaRange(),map={shadow:'rangeLow',highlight:'rangeHigh',shadow_falloff:'falloffLow',highlight_falloff:'falloffHigh',radius:'radius',sensitivity:'sensitivity',inverted:'invert'};
                    for(const k of Object.keys(map)){mutated=true;lr[map[k]]=p[k];}
                }else fail('MASK_NATIVE_ENTRY_MISSING','This mask action is absent from the installed native scripting dictionary');
            }
        }else fail('UNSUPPORTED','Unsupported structured module');
        const after=structured(module);
        for(const k of ['instance','document','image','variant'])if(stable(after.target[k])!==stable(before.target[k]))fail('TARGET_CHANGED','Structured action changed photo identity');
        return {...after,before_state:before.state};
    }
    function moduleApply(){
        const module=q.module,desired=q.desired;
        const before=module in q.scalar_modules?snapshot(q.scalar_modules[module]):structured(module);
        const actual=module in q.scalar_modules?before.values:before.state;
        if(stable(actual)!==stable(q.before_state))fail('STALE_MODULE','Module changed before reset/paste');
        guard();
        if(module in q.scalar_modules){for(const f of Object.keys(desired)){mutated=true;settings()[q.properties[f]]=desired[f];}return snapshot(q.scalar_modules[module]);}
        if(module==='film_grain')setGrain(desired);
        else if(module==='curves')for(const c of curveChannels)setCurve(c,desired.channels[c]);
        else if(module==='levels')for(const c of channels)setLevelChannel(c,desired.channels[c]);
        else if(module==='color_balance')for(const [wheel,prefix] of Object.entries({master:'master',shadows:'shadow',midtones:'midtone',highlights:'highlight'})){
            const p={x:desired.values[prefix+'_x'],y:desired.values[prefix+'_y']};if(wheel!=='master')p.lightness=desired.values[prefix+'_lightness'];setBalance(wheel,p);
        }else if(module==='color_mixer'){
            const ce=settings().colorEditorSettings();
            for(let i=ce.advancedColorCorrections().length-1;i>=0;i--){mutated=true;a.delete(ce.advancedColorCorrections.at(i));}
            for(const item of desired.advanced){
                nativeCommands(['make new advanced color correction at color editor settings of adjustments of current layer of primary variant']);
                const row=ce.advancedColorCorrections.at(ce.advancedColorCorrections().length-1);
                for(const key of ['red','green','blue','enabled','hueStart','hueEnd','saturationStart','saturationEnd','smoothness','hueChange','saturationChange','lightnessChange']){mutated=true;row[key]=item[key];}
            }
            for(const c of Object.keys(desired.colors))setBasic(c,desired.colors[c]);
        }else fail('UNSUPPORTED','Module reset/paste unsupported');
        return structured(module);
    }
