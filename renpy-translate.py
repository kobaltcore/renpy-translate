#!/usr/bin/env python3

### Warnings ###
import warnings
warnings.filterwarnings("ignore")

### System ###
import os
import re
import sys
import json
import asyncio
import argparse
import requests
from glob import glob
from shutil import rmtree
from collections import defaultdict
from signal import signal, SIGINT, SIG_IGN

### Display ###
from tqdm import tqdm
from colorama import Fore, Back, Style

### GCP Translate API ###
from google.cloud import translate


TRANSLATION_CACHE_FILE = "translation_cache.json"
# TODO: figure out all used tags before running
sys.exit(1)
TAG_PATTERN = r"{/?i}|{/?q}|{/?b}|{/?size}|{/?a(=[A-z0-9:/?.=&#_-]+)?}"
PPC = 20 / 1_000_000  # 20$ per 1M characters


def confirm(text):
    result = input("{}\n[y]es/[n]o: ".format(text)).lower()
    if not result in ["y", "yes"]:
        return False
    return True


def check(args):
    if os.path.exists(args.output_dir):
        if not confirm("The output directory exists. Do you want to overwrite it?"):
            print("Aborted")
            sys.exit(0)
        rmtree(args.output_dir)
    os.makedirs(args.output_dir, exist_ok=True)


class Tag():

    def __init__(self, tag_type):
        self.tag_type = tag_type

    def __repr__(self):
        return 'Tag(type="{}")'.format(self.tag_type)


class TranslationString():

    def __init__(self, content):
        self.content = content
        self.to_language = None
        self.translation = None

    def translate(self, to_language):
        self.to_language = to_language

        if not self.content.strip():
            self.translation = self.content
            return

        cached_translation = self.pull_from_cache(to_language)
        if cached_translation:
            self.translation = cached_translation
            return

        translation = TRANSLATION_CLIENT.translate(self.content, target_language=self.to_language)
        self.translation = translation["translatedText"]
        TRANSLATION_CACHE[self.content] = {to_language: self.translation}

    def pull_from_cache(self, to_language):
        available_translations = TRANSLATION_CACHE.get(self.content, None)
        if available_translations:
            cached_translation = available_translations.get(to_language, None)
            if cached_translation:
                return cached_translation

    def estimate_price(self, to_language):
        if self.pull_from_cache(to_language):
            return (0, 1)
        return (len(self.content) * PPC, 0)

    def __repr__(self):
        return 'TranslationString(content="{}", translation="{}")'.format(self.content, self.translation)


class TranslationItem():

    def __init__(self, source_line=0, target_line=0, original_content="", translated_content=""):
        self.source_line = source_line
        self.target_line = target_line
        self.translation_strings = []
        self.original_content = original_content

    def sanitize(self):
        self.translation_strings = []
        for sub_string in re.sub(TAG_PATTERN, "", self.original_content).split("\\n"):
            self.translation_strings.append(TranslationString(sub_string))

    def translate(self, to_language):
        for translation_string in self.translation_strings:
            translation_string.translate(to_language)

    def get_translated_content(self):
        return "\\n".join([translation_string.translation for translation_string in self.translation_strings])

    @property
    def original_content(self):
        return self._original_content

    @original_content.setter
    def original_content(self, original_content):
        self._original_content = original_content
        self.sanitize()

    def estimate_price(self, to_language):
        data = [item.estimate_price(to_language) for item in self.translation_strings]
        return sum(item[0] for item in data), sum(item[1] for item in data)

    def __iter__(self):
        return iter(self.translation_strings)

    def __repr__(self):
        return "TranslationItem({}, {})".format(self.source_line + 1, self.target_line + 1)


class TranslationBlock():

    def __init__(self, source_file=None, block_line=0):
        self.source_file = source_file
        self.block_line = block_line
        self.translation_items = []

    def add_translation_item(self, translation_item):
        self.translation_items.append(translation_item)

    def translate(self, to_language):
        for item in self.translation_items:
            item.translate(to_language)

    def estimate_price(self, to_language):
        data = [item.estimate_price(to_language) for item in self.translation_items]
        return sum(item[0] for item in data), sum(item[1] for item in data)

    def __iter__(self):
        return iter(self.translation_items)

    def __repr__(self):
        return "TranslationBlock({}, {})".format(self.source_file, self.block_line + 1)


class TranslationFile():

    def __init__(self, filename):
        self.filename = filename
        self.translation_blocks = []

    def add_translation_block(self, translation_block):
        self.translation_blocks.append(translation_block)

    def translate(self, to_language):
        for block in self.translation_blocks:
            block.translate(to_language)

    def estimate_price(self, to_language):
        data = [item.estimate_price(to_language) for item in self.translation_blocks]
        return sum(item[0] for item in data), sum(item[1] for item in data)

    def __iter__(self):
        return iter(self.translation_blocks)

    def __repr__(self):
        return "TranslationFile({}, {} blocks)".format(self.filename, len(self.translation_blocks))


def parse_tags(string):
    tmp_list = []
    for i in range(len(breakpoints)):
        c_start, c_stop = breakpoints[i]
        tmp_list.append(Tag(string[c_start:c_stop]))
        if i + 1 < len(breakpoints):
            n_start, n_stop = breakpoints[i + 1]
            if string[c_stop:n_start]:
                if string[c_stop:n_start] == " ":
                    tmp_list.append(" ")
                tmp_list.append(TranslationString(string[c_stop:n_start]))

    # Add rest of string
    rest = string[breakpoints[-1][1]:]
    if rest:
        if rest.startswith(" "):
            tmp_list.append(" ")
        tmp_list.append(TranslationString(string[breakpoints[-1][1]:].strip()))

    return tmp_list


def main(args):
    global TRANSLATION_CACHE

    ### Caching Setup ###
    if not os.path.isfile(TRANSLATION_CACHE_FILE):
        with open(TRANSLATION_CACHE_FILE, "w") as f:
            json.dump({}, f)

    with open(TRANSLATION_CACHE_FILE, "r") as f:
        TRANSLATION_CACHE = json.load(f)

    ### File Parsing ###
    files = glob(os.path.join(args.input_dir, "**", "*.rpy"), recursive=True)
    file_map = {}

    # Find all translation blocks in all files
    # Create a mapping to the correct line in each file
    print("Parsing files")
    for file in tqdm(files, total=len(files), unit="files"):
        with open(file, "r") as f:
            translation_file = TranslationFile(file)
            translation_block, translation_item, block = None, None, None
            text_lines = f.readlines()
            for i, line in enumerate(text_lines):
                if i != block and re.match(r"translate ([A-z0-9_]+) ([A-z0-9_]+)", line):
                    block = i
                    if translation_block:
                        translation_file.add_translation_block(translation_block)
                    translation_block = TranslationBlock(source_file=file, block_line=i)
                    continue

                if text_lines[i - 1].strip().startswith("#") and line.strip().startswith("old"):
                    m = re.match(r'(\s*)(\w+)?\s*"(.*)"', line.strip())
                    translation_item = TranslationItem(source_line=i, original_content=m.group(3))
                    continue

                if text_lines[i - 1].strip().startswith("old") and line.strip().startswith("new"):
                    translation_item.target_line = i
                    translation_block.add_translation_item(translation_item)
                    continue

                if text_lines[i - 1].strip().startswith("#") \
                        and not text_lines[i - 1].strip().startswith("# nvl clear") \
                        and line.strip():
                    m = re.match(r'(\s*)(\w+)?\s*"(.*)"', text_lines[i - 1].strip()[2:])
                    if line.strip().startswith("nvl clear"):
                        translation_item = TranslationItem(source_line=i,
                                                           original_content=m.group(3),
                                                           target_line=i + 1)
                    else:
                        translation_item = TranslationItem(source_line=i,
                                                           original_content=m.group(3),
                                                           target_line=i)
                    translation_block.add_translation_item(translation_item)
                    continue

            if translation_block:
                translation_file.add_translation_block(translation_block)
            file_map[file] = translation_file

    ### Price Estimation ###

    price, cache_hits = 0, 0
    for file, translation_file in file_map.items():
        _price, _cache_hits = translation_file.estimate_price(args.target_language)
        price += _price
        cache_hits += _cache_hits

    if price < 1:
        print("Estimated cost: {}{:.2f} cents{} ({}{} cache hits{})".format(Fore.RED, price * 100,
                                                                            Style.RESET_ALL, Fore.GREEN,
                                                                            cache_hits, Style.RESET_ALL))
    else:
        print("Estimated cost: {}{:.2f}${} ({}{} cache hits{})".format(Fore.RED, price, Style.RESET_ALL,
                                                                       Fore.GREEN, cache_hits, Style.RESET_ALL))
    print("{}Note{}: The estimated cost may be less than the full cost if cached translations are available.".format(
        Fore.RED, Style.RESET_ALL))
    if not confirm("Do you want to start the translation? ({}This will incur the calculated cost{})".format(Fore.RED, Style.RESET_ALL)):
        print("Aborted")
        sys.exit(1)

    ### Translation ###

    print("Starting translation")
    for file, translation_file in tqdm(file_map.items(), total=len(file_map.keys()), unit="files"):
        tqdm.write("Translating '{}' {}\u2713 OK{}".format(translation_file, Fore.GREEN, Style.RESET_ALL))
        translation_file.translate(args.target_language)

    ### Writeback to disk ###

    print("Saving translations")
    for file, translation_file in tqdm(file_map.items(), total=len(file_map.keys()), unit="files"):
        with open(file, "r") as f:
            text_lines = f.readlines()
            for block in translation_file:
                for item in block:
                    m = re.match(r"(\s*)(\w+)?\s*("")", text_lines[item.target_line])
                    text_lines[item.target_line] = '{}{} "{}"\n'.format(m.group(1),
                                                                        m.group(2),
                                                                        item.get_translated_content())
        common_path = os.path.commonpath([os.path.abspath(file), os.path.abspath(args.input_dir)])
        out_path = os.path.join(args.output_dir, os.path.relpath(file, common_path))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.writelines(text_lines)

    print("Persisting Cache")
    with open(TRANSLATION_CACHE_FILE, "w") as f:
        json.dump(TRANSLATION_CACHE, f, indent=4)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="A tool for translating Ren'Py translation script to different languages.")
    parser.add_argument("-i", "--input", type=str, dest="input_dir", required=True,
                        metavar="dir", help="(required) The directory containing the extracted Ren'Py translations")
    parser.add_argument("-l", "--language", type=str, dest="target_language", required=True,
                        metavar="language", help="(required) The language to translate to")
    parser.add_argument("-a", "--api_file", type=str, dest="api_file", required=True,
                        metavar="api_file", help="(required) Your GCP API JSON file")
    parser.add_argument("-o", "--output", type=str, dest="output_dir", required=True,
                        metavar="dir", help="(required) The directory to output data to")
    args = parser.parse_args()

    original_sigint_handler = signal(SIGINT, SIG_IGN)
    signal(SIGINT, original_sigint_handler)

    check(args)

    print("Loading translation client")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = args.api_file
    TRANSLATION_CLIENT = translate.Client()

    available_languages = [d["language"] for d in TRANSLATION_CLIENT.get_languages()]
    if not args.target_language in available_languages:
        print("'{}' is not a supported language.\nValid languages are: {}".format(
            args.target_language, ", ".join(available_languages)))
        sys.exit(1)

    try:
        main(args)
    except KeyboardInterrupt:
        print("\nReceived SIGINT, terminating...")

    print("Done.")
