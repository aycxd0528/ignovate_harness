import {packager} from '@electron/packager';
const [directory,runtime,tools,output,version,electronVersion,electronZipDir]=process.argv.slice(2);
if(!directory||!electronVersion)throw Error('Expected app, runtime, tools, output, version and Electron version');
const paths=await packager({dir:directory,name:'Ignovate',platform:'darwin',arch:'arm64',electronVersion,appVersion:version,
 appBundleId:'com.ignovate.harness',appCategoryType:'public.app-category.developer-tools',darwinDarkModeSupport:true,
 extraResource:[tools],out:output,overwrite:false,prune:false,asar:false,...(electronZipDir?{electronZipDir}:{} )});
for(const path of paths)process.stdout.write(path+'\n');
