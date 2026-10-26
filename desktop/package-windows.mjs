import {packager} from '@electron/packager';
import {access} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const [directory,runtime,tools,output,version,electronVersion,electronZipDir]=process.argv.slice(2);
if(!directory||!electronVersion)throw Error('Expected app, runtime, tools, output, version and Electron version');
const icon=fileURLToPath(new URL('./assets/ignovate.ico',import.meta.url));
await access(icon);
const paths=await packager({dir:directory,name:'Ignovate',platform:'win32',arch:'x64',electronVersion,appVersion:version,
 icon,
 win32metadata:{CompanyName:'Ignovate',FileDescription:'Ignovate Harness',ProductName:'Ignovate'},
 extraResource:[runtime,tools],out:output,overwrite:false,prune:false,asar:false,...(electronZipDir?{electronZipDir}:{} )});
for(const path of paths)process.stdout.write(path+'\n');
