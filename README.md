# renpy-translate
This script uses the Google Cloud Platform [Translation API](https://cloud.google.com/translate) to translate Ren'Py-generated translation files. It can batch-process thousands of words per second to give you a quick baseline translation of your entire game in one fell swoop.

Please be aware that you need to have a Google Cloud Platform account to use this script.  
Currently, the API charges 20$ per 1 Million characters.

The tool is intelligent in that it will cache successful translations so that you only ever pay once for a specific piece of text, no matter how often you run this tool. It will also factor the cached translations into the cost calculation to give you an accurate price estimation before the actual translation process begins.

## Disclaimer
This tool is still in Beta and as such bugs may occur and translations may fail. Since the script requires access to a paid API it is advised to not use it on large projects or at the very least only for evaluation purposes until it is deemed stable enough for production use.  
The tool is not affiliated with Ren'Py in any way and does not use any code from the engine to parse the translation files. As such future updates to Ren'Py in general and the translation format specifically may break this tool.

## Installation
```bash
$ git clone https://github.com/kobaltcore/renpy-translate.git
$ cd renpy-translate/
$ virtualenv venv -p python3
$ source venv/bin/activate
$ pip install -r requirements.txt
```

## Usage
```bash
usage: renpy-translate.py [-h] -i dir -l language -a api_file -o dir

A tool for translating RenPy translation script to different languages
via the Google Cloud Platform Translate API.

optional arguments:
  -h, --help            show this help message and exit
  -i dir, --input dir   (required) The directory containing the extracted
                        RenPy translations
  -l language, --language language
                        (required) The language to translate to
  -a api_file, --api_file api_file
                        (required) Your GCP API JSON file
  -o dir, --output dir  (required) The directory to output data to
```
