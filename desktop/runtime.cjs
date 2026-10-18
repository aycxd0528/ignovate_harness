const path=require('node:path');const fs=require('node:fs');
function runtimePaths({packaged,resourcesPath,sourceRoot,platform=process.platform,environment=process.env,exists=fs.existsSync}){
 const local=path.join(sourceRoot,'.venv',platform==='win32'?'Scripts/python.exe':'bin/python');
 const bundled=path.join(resourcesPath||sourceRoot,'runtime',platform==='win32'?'python.exe':'bin/python3');
 return {python:environment.IGNOVATE_PYTHON||(packaged?bundled:exists(local)?local:platform==='win32'?'python':'python3'),pythonArgs:packaged?['-I','-B']:[]};
}
module.exports={runtimePaths};
