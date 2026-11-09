"""Return extension registration mutates only disposable fixture state."""
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ExtensionTests(unittest.TestCase):
    def test_return_script_loads_configuration_relative_to_its_package(self):
        import subprocess
        script = ROOT / 'integrations/hermes-mode/return-to-odysseus.js'
        code = r"""
const fs=require('fs'),vm=require('vm');
let requested;
const script=process.argv[1];
vm.runInNewContext(fs.readFileSync(script,'utf8'),{
 URL,AbortSignal,console,
 location:{protocol:'http:',hostname:'127.0.0.1',port:'8787',origin:'http://127.0.0.1:8787'},
 document:{baseURI:'http://127.0.0.1:8787/',currentScript:{src:'http://127.0.0.1:8787/extensions/odysseus-mode/return-to-odysseus.js'}},
 fetch:(url)=>{requested=String(url);return new Promise(()=>{});}
});
console.log(requested);
"""
        result = subprocess.check_output(['node', '-e', code, str(script)], text=True).strip()
        self.assertEqual(result, 'http://127.0.0.1:8787/extensions/odysseus-mode/connection.json')

    def test_return_script_targets_localhost_when_served_through_the_proxy_host(self):
        import subprocess
        script = ROOT / 'integrations/hermes-mode/return-to-odysseus.js'
        code = r"""
const fs=require('fs'),vm=require('vm');
let href;const requested=[],hrefs={};
const element=()=>{const e={setAttribute(){},removeAttribute(){},getAttribute(){return null},addEventListener(){},append(){}};
 Object.defineProperty(e,'href',{set(v){e._href=v;if(e.id==='odysseus-return-control')href=v;hrefs[e.id]=v},get(){return e._href}});return e;};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{
 URL,AbortSignal,console:{warn(){}},
 location:{protocol:'http:',hostname:'hermes.localhost',port:'7000',origin:'http://hermes.localhost:7000'},
 document:{baseURI:'http://hermes.localhost:7000/',createElement:element,body:{append(){}}},
 fetch:(url)=>{requested.push(String(url));return new Promise(()=>{});}
});
setImmediate(()=>console.log(JSON.stringify({href,requested,providers:hrefs['odysseus-providers-link']})));
"""
        result = json.loads(subprocess.check_output(['node', '-e', code, str(script)], text=True))
        self.assertEqual(result, {'href': 'http://localhost:7000/', 'requested': [], 'providers': 'http://localhost:7000/static/provider-sync.html'})

    def test_registration_preserves_other_extensions_and_is_idempotent(self):
        import src.hermes_component as component
        self.assertTrue(hasattr(component, 'register_return_extension'), 'persistent return registration is missing')
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            state = Path(directory)
            metadata = state / 'extension-install-manifest.json'
            other = {'version': 'custom', 'files': ['other.js'], 'installed_at': 'retained'}
            metadata.write_text(json.dumps({'version': 1, 'installed': {'custom': other}}))
            for _ in range(2):
                component.register_return_extension(state, ROOT / 'integrations/hermes-mode', 'http://127.0.0.1:7000/')
            installed = json.loads(metadata.read_text())['installed']
            self.assertEqual(installed['custom'], other)
            self.assertIn('odysseus-mode', installed)
            package = state / 'extensions/odysseus-mode'
            self.assertTrue((package / 'manifest.json').is_file())
            self.assertEqual(json.loads((package / 'connection.json').read_text())['odysseus_url'], 'http://127.0.0.1:7000/')

    def test_corrupt_registry_and_unowned_package_are_not_overwritten(self):
        import src.hermes_component as component
        self.assertTrue(hasattr(component, 'register_return_extension'), 'persistent return registration is missing')
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            state = Path(directory)
            metadata = state / 'extension-install-manifest.json'
            metadata.write_text('{broken')
            with self.assertRaises(component.ComponentError):
                component.register_return_extension(state, ROOT / 'integrations/hermes-mode', 'http://127.0.0.1:7000/')
            self.assertEqual(metadata.read_text(), '{broken')
            metadata.unlink()
            package = state / 'extensions/odysseus-mode'
            package.mkdir(parents=True)
            (package / 'personal.txt').write_text('keep')
            with self.assertRaises(component.ComponentError):
                component.register_return_extension(state, ROOT / 'integrations/hermes-mode', 'http://127.0.0.1:7000/')
            self.assertEqual((package / 'personal.txt').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
