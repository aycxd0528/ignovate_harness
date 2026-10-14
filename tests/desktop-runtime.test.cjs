const {test}=require('node:test');const assert=require('node:assert/strict');const path=require('node:path');
const {runtimePaths}=require('../desktop/runtime.cjs');
test('packaged macOS uses its embedded Python and isolated module imports',()=>{
 const result=runtimePaths({packaged:true,resourcesPath:'/Applications/Ignovate.app/Contents/Resources',sourceRoot:'/unused',platform:'darwin',environment:{}});
 assert.equal(result.python,'/Applications/Ignovate.app/Contents/Resources/runtime/bin/python3');assert.deepEqual(result.pythonArgs,['-I','-B']);
});
test('source launch retains the existing project virtualenv and explicit override',()=>{
 const options={packaged:false,resourcesPath:'/unused',sourceRoot:'/project',platform:'darwin',environment:{},exists:()=>true};
 assert.equal(runtimePaths(options).python,path.join('/project','.venv/bin/python'));assert.deepEqual(runtimePaths(options).pythonArgs,[]);
 assert.equal(runtimePaths({...options,environment:{IGNOVATE_PYTHON:'/custom/python'}}).python,'/custom/python');
});
test('source without a virtualenv retains the platform Python fallback',()=>{
 assert.equal(runtimePaths({packaged:false,sourceRoot:'/project',platform:'win32',environment:{},exists:()=>false}).python,'python');
});
