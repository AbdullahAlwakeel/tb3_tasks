import os
import importlib.util

spec=importlib.util.spec_from_file_location('extract_secrets','working_temp/extract_secrets.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

img='puzzle_0001.png'
path=os.path.join('.',img)
for max_offset in [256,1024,4096,16384,65536]:
    try:
        sec=mod.extract_secret_for_image(path, max_offset_bits=max_offset, steps=(1,2,3,4))
    except Exception as e:
        sec=f"ERR {type(e).__name__}: {e}"
    print('max_offset',max_offset,'->',sec)
