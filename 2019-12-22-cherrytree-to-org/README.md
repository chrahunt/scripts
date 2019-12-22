# cherrytree-to-org

Script to convert from CherryTree's XML export format to a single org
file in a git repository, with a commit for each node's created and
modified times.

# usage

* Click CherryTree > Export > Export to CherryTree Document > All the Tree > OK
* XML, Not protected > OK
* Select file

Then

```
python -m venv env
./env/bin/pip install -r requirements.txt
./env/bin/python t.py export.ctd output_dir
```
