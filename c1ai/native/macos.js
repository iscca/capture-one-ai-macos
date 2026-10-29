ObjC.import('AppKit');
function run(argv) {
    const q = JSON.parse(argv[0]);
    const a = Application('/Applications/Capture One.app');
    let mutated = false;
    function stable(o) { if (o === null || typeof o !== 'object') return JSON.stringify(o); return '{' + Object.keys(o).sort().map(k=>JSON.stringify(k)+':'+stable(o[k])).join(',') + '}'; }
    function fail(code, message) { const e = new Error(message); e.code = code; throw e; }
    function environment() {
        const r = $.NSRunningApplication.runningApplicationsWithBundleIdentifier('com.captureone.captureone16').firstObject;
        if (!r) fail('APP_NOT_RUNNING', 'Open Capture One first');
        const docs = a.documents();
        const d = docs.length ? a.currentDocument() : null;
        return {instance:{pid:Number(r.processIdentifier),started_at:ObjC.unwrap(r.launchDate.description),version:a.version()},
            document:d ? {id:d.id(),path:String(d.path()),kind:d.kind()} : null,
            selected_count:a.selectedVariants().length};
    }
    function snapshot(fields) {
        const env = environment();
        if (!env.document || env.selected_count !== 1) fail('SINGLE_SELECTION_REQUIRED', 'Select exactly one photo in the intended Session');
        const v = a.primaryVariant();
        const layer = v.currentLayer();

        const target = {instance:env.instance,document:env.document,image:{path:String(v.parentImage().path())},
                        variant:{id:String(v.id()),label:v.name()},layer:layer.kind()==='background'?'Background':selectedLayerRef()};
        let values = {};
        for (const f of fields) values[f] = scalarValue(f);
        if (stable(target) !== stable(identity())) fail('TARGET_CHANGED','Target changed during native read');
        return {target:target,values:values,processing_engine:v.engine(),native_unset_fields:fields.filter(f=>settings()[q.properties[f]]()===null)};
    }
    function identity() {
        const env = environment();
        if (!env.document || env.selected_count !== 1) fail('SINGLE_SELECTION_REQUIRED','Selection changed');
        const v = a.primaryVariant();

        return {instance:env.instance,document:env.document,image:{path:String(v.parentImage().path())},
                variant:{id:String(v.id()),label:v.name()},layer:v.currentLayer().kind()==='background'?'Background':selectedLayerRef()};
    }
    function guard() {
        if (stable(identity()) !== stable(q.expected)) fail('TARGET_CHANGED','Native target changed before dispatch');
    }
    // MACOS_TOOLS
    try {
        if (q.action === 'context') {
            const env = environment();
            let target = null;
            let selectionError = null;
            if (env.selected_count === 1) {
                try { target = snapshot([]).target; } catch(e) { selectionError = {code:e.code || 'NATIVE_READ_FAILED',message:String(e)}; }
            }
            return JSON.stringify({status:'ok',...env,target:target,selection_error:selectionError});
        }
        if (q.action === 'read') return JSON.stringify({status:'ok',...snapshot(q.fields)});
        if(q.action==='inventory')return JSON.stringify({status:'ok',...rawInventory(q.scope||'filtered')});
        if(q.action==='tool_read')return JSON.stringify({status:'ok',...toolRead()});
        if(q.action==='navigate'){
            const current=rawInventory(q.scope||'filtered');
            if(stable(current)!==stable(q.inventory))fail('STALE_COLLECTION','Collection or selection changed before navigation');
            if(q.expected)guard();
            const ids=current.items.map(r=>r.variant_uuid),selection=current.selected_ids;
            let id=q.variant_uuid;
            if(q.direction){if(selection.length!==1)fail('SINGLE_SELECTION_REQUIRED','Next/previous require one selected photo');
                const index=ids.indexOf(selection[0])+(q.direction==='next'?1:-1);if(index<0||index>=ids.length)fail('COLLECTION_BOUNDARY','Already at collection boundary');id=ids[index];}
            if(!ids.includes(id))fail('IMAGE_NOT_FOUND','Variant is outside the observed collection');
            const d=a.currentDocument(),all=d.variants.id().map(String),v=d.variants.at(all.indexOf(id));
            mutated=true;a.deselect(d,{variants:a.selectedVariants()});a.select(d,{variant:v});
            const after=snapshot([]);if(after.target.variant.id!==id)fail('SELECTION_CHANGED','Native selected variant differs');
            return JSON.stringify({status:'ok',...after});
        }
        guard();
        if(q.action==='inspect_export'){
            const v=a.primaryVariant(),paths=v.outputEvents.path(),dates=v.outputEvents.date();
            const index=paths.indexOf(q.path);
            return JSON.stringify({status:'ok',target:identity(),matched:index>=0,
                event_time:index>=0?dates[index].getTime()/1000:null});
        }
        if(q.action==='tool_adjust')return JSON.stringify({status:'ok',...toolAdjust()});
        if(q.action==='module_apply')return JSON.stringify({status:'ok',...moduleApply()});
        if (q.action === 'set') {
            let before = snapshot(q.fields);
            for (const f of q.fields) if (before.values[f] !== q.before[f]) fail('STALE_VALUES','Native values changed before write');
            const v = a.primaryVariant();
            for (const f of Object.keys(q.patch)) {
                guard();
                const currentValue = scalarValue(f);
                // C1's temperature setter slightly changes tint through native WB conversion.
                // Keep the full exact initial precondition, then bound only this observed coupling.
                const ownWBRounding = mutated && f === 'tint' && q.patch.temperature !== undefined
                    && Math.abs(currentValue - q.before[f]) <= 0.002;
                if (currentValue !== q.before[f] && !ownWBRounding) fail('STALE_VALUES','Native value changed before setter');
                mutated = true;
                settings()[q.properties[f]] = q.patch[f];
            }
            guard();
            return JSON.stringify({status:'ok',...snapshot(q.fields)});
        }
        if (q.action === 'clone') {
            const source = a.primaryVariant();
            const oldIds = source.parentImage().variants().map(v=>String(v.id()));
            guard();
            mutated = true;
            const clone = a.cloneVariant(source,{additiveSelect:false});
            const id = String(clone.id());
            if (oldIds.indexOf(id) !== -1) fail('CLONE_UNVERIFIED','Clone returned an existing variant');
            a.deselect(a.currentDocument(), {variants:a.selectedVariants()});
            a.select(a.currentDocument(), {variant:clone});
            const after = snapshot([]);
            if (oldIds.indexOf(id) !== -1 || after.target.variant.id !== id
                || after.target.image.path !== q.expected.image.path
                || stable(after.target.document) !== stable(q.expected.document)
                || stable(after.target.instance) !== stable(q.expected.instance))
                fail('CLONE_UNVERIFIED','Created variant identity or selection could not be verified');
            return JSON.stringify({status:'ok',...after,created_id:id});
        }
        if (q.action === 'confirm_candidate') {
            const current = identity();
            if (stable(current.document) !== stable(q.source.document) || current.image.path !== q.source.image.path
                || stable(current.instance) !== stable(q.source.instance)) fail('TARGET_CHANGED','Candidate belongs to another photo or instance');
            const variants = a.primaryVariant().parentImage().variants();
            const source = variants.filter(v=>String(v.id())===q.source.variant.id);
            const candidates = variants.filter(v=>String(v.id())===q.candidate_id);
            if (source.length !== 1 || candidates.length !== 1 || q.candidate_id === q.source.variant.id)
                fail('CANDIDATE_UNVERIFIED','Source and confirmed candidate must be distinct native variants');
            mutated = true;
            a.deselect(a.currentDocument(), {variants:a.selectedVariants()});
            a.select(a.currentDocument(), {variant:candidates[0]});
            const after = snapshot([]);
            if (after.target.variant.id !== q.candidate_id) fail('SELECTION_CHANGED','Confirmed candidate is not primary');
            return JSON.stringify({status:'ok',...after,created_id:q.candidate_id});
        }
        if (q.action === 'export') {
            const d = a.currentDocument();
            const v = a.primaryVariant();
            const baseline = revisionState();
            let recipe = null;
            let finished = false;
            let jobSubmitted = false;
            try {
                mutated = true;
                // Remove only completed orphan previews in this owner's private archive.
                if(d.jobs.id().length===0){
                    const paths=v.outputEvents.path();
                    for(const name of d.recipes.name().filter(n=>/^C1AI-preview-[a-f0-9]{20}$/.test(n))){
                        const old=d.recipes.byName(name),location=String(old.rootFolderLocation());
                        if(q.archive_root && location.startsWith(q.archive_root+'/') && paths.includes(location+'/preview.jpg')
                            && $.NSFileManager.defaultManager.fileExistsAtPath(location+'/preview.jpg'))a.delete(old);
                    }
                }
                recipe = a.make({new:'recipe',at:d,withProperties:{name:q.recipe_name}});
                recipe.outputFormat = 'JPEG';
                recipe.jpegQuality = 90;
                recipe.colorProfile = 'sRGB IEC61966-2.1';
                recipe.scalingMethod = 'Long_Edge';
                recipe.scalingUnit = 'pixels';
                recipe.primaryScalingValue = q.max_size;
                recipe.upscale = false;
                recipe.rootFolderLocation = Path(q.output_dir);
                recipe.rootFolderType = 'custom location';
                recipe.outputNameFormat = q.output_name;
                recipe.outputSubFolder = '';
                recipe.existingFiles = 'skip';
                if (recipe.rootFolderType() !== 'custom location' || String(recipe.rootFolderLocation()) !== q.output_dir
                    || recipe.primaryScalingValue() !== q.max_size || recipe.scalingMethod() !== 'Long_Edge'
                    || recipe.outputFormat() !== 'JPEG' || recipe.outputNameFormat() !== q.output_name)
                    fail('RECIPE_UNVERIFIED','Recipe destination, format or dimensions differ from request');
                guard();
                jobSubmitted = true;
                const job = a.process(v,{recipe:q.recipe_name});
                if (job.indexOf('ERROR') === 0) fail('EXPORT_FAILED',job);
                const end = Date.now() + 70000;
                while (d.jobs.id().includes(job) || !$.NSFileManager.defaultManager.fileExistsAtPath(q.output_dir+'/'+q.output_name+'.jpg')) {
                    if (Date.now() > end) fail('EXPORT_TIMEOUT','Export job remains in queue; query original request');
                    $.NSThread.sleepForTimeInterval(0.2);
                }
                guard();
                if (baseline !== revisionState())
                    fail('STALE_PREVIEW','Adjustments changed during export');
                finished = true;
                return JSON.stringify({status:'ok',job_id:job,target:identity(),revision:baseline,
                    limits:'Checks script-exposed curves, levels, mixers, color balance, scalar/layer/luma settings; mask pixels and atomic revision counters are unavailable; no coordinate mapping'});
            } finally {
                // Completed jobs no longer need the temporary recipe; unknown jobs retain evidence.
                if (recipe && (finished || !jobSubmitted)) a.delete(recipe);
            }
        }
        fail('UNSUPPORTED','Unsupported Mac operation');
    } catch(e) {
        let observation=null;
        if(mutated && q.action==='tool_adjust'){try{observation=structured(q.module);}catch(ignore){}}
        return JSON.stringify({status:mutated?'outcome_unknown':'refused',observation:observation,
            error:{code:e.code || 'APPLE_EVENT_FAILED',message:String(e)},dispatched:mutated});
    }
}
