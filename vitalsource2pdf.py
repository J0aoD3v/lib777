#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import sys
from pathlib import Path

sys.path.append('/content')

import img2pdf
from PIL import Image
from pypdf import PdfMerger, PdfReader, PdfWriter
from pagelabels import PageLabelScheme, PageLabels
from pdfrw import PdfReader as pdfrw_reader
from pdfrw import PdfWriter as pdfrw_writer
from selenium.webdriver import ActionChains, Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from seleniumwire import webdriver
from tqdm import tqdm
from webdriver_manager.chrome import ChromeDriverManager

from fucts.roman import move_romans_to_front, roman_sort_with_ints, try_convert_int

parser = argparse.ArgumentParser()
parser.add_argument('--output', default='./output/')
parser.add_argument('--yuzu', default=False)
parser.add_argument('--isbn', required=True)
parser.add_argument('--delay', default=2, type=int)
parser.add_argument('--pages', default=None, type=int)
parser.add_argument('--start-page', default=0, type=int)
parser.add_argument('--end-page', default=-1, type=int)
parser.add_argument('--chrome-exe', default=None, type=str)
parser.add_argument('--disable-web-security', action='store_true')
parser.add_argument('--language', default='eng')
parser.add_argument('--skip-scrape', action='store_true')
parser.add_argument('--only-scrape-metadata', action='store_true')
parser.add_argument('--skip-ocr', action='store_true')
parser.add_argument('--compress', action='store_true')
args = parser.parse_args()

args.output = Path(args.output)
args.output.mkdir(exist_ok=True, parents=True)
ebook_files = args.output / args.isbn
ebook_files.mkdir(exist_ok=True, parents=True)

book_info = {}
non_number_pages = 0

platform_identifiers = {
    'home_url': "https://app.minhabiblioteca.com.br",
    'jigsaw_url': "https://app.minhabiblioteca.com.br/api",
    'total_pages': "page-info",  # Substitua pela classe correta
    'current_page': "current-page-input",  # Substitua
    'page_loader': "loading-spinner",  # Substitua
    'next_page': "next-page-button",  # Substitua
}

def get_num_pages():
    while True:
        try:
            total = int(driver.execute_script(f'return document.getElementsByClassName("{platform_identifiers["total_pages"]}")[0].innerHTML').strip().split('/')[-1].strip())
            try:
                current_page = driver.execute_script(f'return document.getElementsByClassName("{platform_identifiers["current_page"]}")[0].value')
                if current_page == '' or not current_page:
                    current_page = 0
            except Exception:
                current_page = 0
            return current_page, total
        except Exception:
            time.sleep(1)

def load_book_page(page_id):
    driver.get(f"{platform_identifiers['home_url']}/reader/books/{args.isbn}/pageid/{page_id}")
    get_num_pages()
    while len(driver.find_elements(By.CLASS_NAME, platform_identifiers['page_loader'])):
        time.sleep(1)

if not args.skip_scrape or args.only_scrape_metadata:
    chrome_options = webdriver.ChromeOptions()
    if args.disable_web_security:
        chrome_options.add_argument('--disable-web-security')
        print('DESATIVADA SEGURANÇA WEB!')
    chrome_options.add_argument('--disable-http2')
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    if args.chrome_exe:
        chrome_options.binary_location = args.chrome_exe
    else:
        chrome_options.binary_location = '/usr/lib/chromium-browser/chrome'  # Caminho alternativo
    seleniumwire_options = {'disable_encoding': True}
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options,
        seleniumwire_options=seleniumwire_options
    )

    driver.get(platform_identifiers['home_url'])
    input('Pressione ENTER após fazer login...')

    driver.maximize_window()
    page_num = args.start_page
    load_book_page(page_num)

    print('Raspando metadados...')
    time.sleep(args.delay * 2)
    failed = True
    for i in range(5):
        for request in driver.requests:
            if request.url == f"{platform_identifiers['jigsaw_url']}/books/{args.isbn}/pages":
                wait = 0
                while not request.response and wait < 30:
                    time.sleep(1)
                    wait += 1
                if not request.response or not request.response.body:
                    print('Falha ao obter informações das páginas.')
                else:
                    book_info['pages'] = json.loads(request.response.body.decode())
            elif request.url == f"{platform_identifiers['jigsaw_url']}/info/books.json?isbns={args.isbn}":
                wait = 0
                while not request.response and wait < 30:
                    time.sleep(1)
                    wait += 1
                if not request.response or not request.response.body:
                    print('Falha ao obter informações do livro.')
                else:
                    book_info['book'] = json.loads(request.response.body.decode())
            elif request.url == f"{platform_identifiers['jigsaw_url']}/books/{args.isbn}/toc":
                wait = 0
                while not request.response and wait < 30:
                    time.sleep(1)
                    wait += 1
                if not request.response or not request.response.body:
                    print('Falha ao obter informações do TOC, obtido apenas:', list(book_info.keys()))
                else:
                    book_info['toc'] = json.loads(request.response.body.decode())
        if all(key in book_info for key in ['pages', 'book', 'toc']):
            failed = False
        else:
            print('Faltando alguns dados do livro, obtido apenas:', list(book_info.keys()))
        if not failed:
            break
        print('Tentando novamente a raspagem de metadados em 10s...')
        load_book_page(page_num)
        time.sleep(10)

    if args.only_scrape_metadata:
        driver.close()
        del driver

    if not args.only_scrape_metadata:
        _, total_pages = get_num_pages()

        if args.start_page > 0:
            print('Você especificou uma página inicial, ignorando a contagem total.')
            total_pages = 99999999999999999

        print('Número total de páginas:', total_pages)
        print('Raspando páginas...')

        page_urls = set()
        failed_pages = set()
        bar = tqdm(total=total_pages)
        bar.update(page_num)
        while page_num < total_pages + 1:
            time.sleep(args.delay)
            retry_delay = 5
            base_url = None
            for page_retry in range(3):
                for find_img_retry in range(3):
                    for request in driver.requests:
                        if request.url.startswith(f"{platform_identifiers['jigsaw_url']}/books/{args.isbn}/images/"):
                            base_url = '/'.join(request.url.split('/')[:-1])
                    time.sleep(1)
                if base_url:
                    break
                bar.write(f'Não foi possível encontrar uma imagem para a página {page_num}, aguardando {retry_delay}s...')
                time.sleep(retry_delay)
                retry_delay += 5

            page, _ = get_num_pages()

            if not base_url:
                bar.write(f'Falha ao obter URL para a página {page_num}, tentando novamente depois.')
                failed_pages.add(page_num)
            else:
                page_urls.add((page, base_url))
                bar.write(base_url)
                try:
                    int(page)
                except ValueError:
                    total_pages += 1
                    non_number_pages += 1
                    bar.write(f'Página não numerada {page}, aumentando contagem em 1 para: {total_pages}')
                    bar.total = total_pages
                    bar.refresh()

            if page_num == args.end_page:
                bar.write(f'Saindo na página {page_num}.')
                break

            if isinstance(page_num, int) and page_num > 0:
                try:
                    if driver.execute_script(f'return document.getElementsByClassName("{platform_identifiers["next_page"]}")[0].disabled'):
                        bar.write('Livro concluído, saindo.')
                        break
                except Exception:
                    pass

            del driver.requests
            actions = ActionChains(driver)
            actions.send_keys(Keys.RIGHT)
            actions.perform()
            bar.update()
            page_num += 1
        bar.close()

        print('Refazendo páginas com falha...')
        bar = tqdm(total=len(failed_pages))
        for page in failed_pages:
            load_book_page(page)
            time.sleep(args.delay)
            retry_delay = 5
            base_url = None
            for page_retry in range(3):
                for find_img_retry in range(3):
                    for request in driver.requests:
                        if request.url.startswith(f"{platform_identifiers['jigsaw_url']}/books/{args.isbn}/images/"):
                            base_url = '/'.join(request.url.split('/')[:-1])
                    time.sleep(1)
                if base_url:
                    break
                bar.write(f'Não foi possível encontrar uma imagem para a página {page}, aguardando {retry_delay}s...')
                time.sleep(retry_delay)
                retry_delay += 5
            page, _ = get_num_pages()
            if not base_url:
                bar.write(f'Falha ao obter URL para a página {page}, tentando novamente depois.')
                failed_pages.add(page)
            else:
                page_urls.add((page, base_url))
                bar.write(base_url)
                del driver.requests
            bar.update(1)
        bar.close()

        print('Todas as páginas raspadas! Baixando imagens...')
        bar = tqdm(total=len(page_urls))
        for page, base_url in page_urls:
            success = False
            for retry in range(6):
                del driver.requests
                time.sleep(args.delay / 2)
                driver.get(f'{base_url.strip("/")}/2000')
                time.sleep(args.delay / 2)
                img_data = None
                for page_retry in range(3):
                    for find_img_retry in range(3):
                        for request in driver.requests:
                            if request.url.startswith(f"{platform_identifiers['jigsaw_url']}/books/{args.isbn}/images/"):
                                img_data = request.response.body
                                break
                dl_file = ebook_files / f'{page}.jpg'
                if img_data:
                    with open(dl_file, 'wb') as file:
                        file.write(img_data)
                    img = Image.open(dl_file)
                    if img.width != 2000:
                        bar.write(f'Imagem muito pequena com {img.width}px de largura, tentando novamente: {base_url}')
                        driver.get('https://google.com')
                        time.sleep(8)
                        load_book_page(0)
                        time.sleep(8)
                        continue
                    img.save(dl_file, format='JPEG', subsampling=0, quality=100)
                    del img
                    success = True
                if success:
                    break
            if not success:
                bar.write(f'Falha ao baixar imagem: {base_url}')
            bar.update()
        bar.close()
        driver.close()
        del driver
else:
    print('Raspagem de páginas ignorada...')

print('Verificando páginas em branco...')
existing_page_files = move_romans_to_front(roman_sort_with_ints([try_convert_int(str(x.stem)) for x in list(ebook_files.iterdir())]))
if non_number_pages == 0:
    for item in existing_page_files:
        if isinstance(try_convert_int(item), str):
            non_number_pages += 1
for page in tqdm(iterable=existing_page_files):
    page_i = try_convert_int(page)
    if isinstance(page_i, int) and page_i > 0:
        page_i += non_number_pages
        last_page_i = try_convert_int(existing_page_files[page_i - 1])
        if isinstance(last_page_i, int):
            last_page_i = last_page_i + non_number_pages
            if last_page_i != page_i - 1:
                img = Image.new('RGB', (2000, 2588), (255, 255, 255))
                img.save(ebook_files / f'{int(page) - 1}.jpg')
                tqdm.write(f'Criada imagem em branco para a página {int(page) - 1}.')

print('Construindo PDF...')
raw_pdf_file = args.output / f'{args.isbn} RAW.pdf'
existing_page_files = move_romans_to_front(roman_sort_with_ints([try_convert_int(str(x.stem)) for x in list(ebook_files.iterdir())]))
page_files = [str(ebook_files / f'{x}.jpg') for x in existing_page_files]
pdf = img2pdf.convert(page_files)
with open(raw_pdf_file, 'wb') as f:
    f.write(pdf)

if 'book' in book_info and 'books' in book_info['book'] and len(book_info['book']['books']):
    title = book_info['book']['books'][0]['title']
    author = book_info['book']['books'][0]['author']
else:
    title = args.isbn
    author = 'Desconhecido'

if not args.skip_ocr:
    print('Executando OCR...')
    ocr_in = raw_pdf_file
    _, raw_pdf_file = tempfile.mkstemp()
    subprocess.run(f'ocrmypdf -l {args.language} --title "{title}" --jobs 4 --output-type pdfa "{ocr_in}" "{raw_pdf_file}"', shell=True)
else:
    ebook_output_ocr = args.output / f'{args.isbn}.pdf'
    print('Ignorando OCR...')

print('Adicionando metadados...')
file_in = open(raw_pdf_file, 'rb')
pdf_reader = PdfReader(file_in)
pdf_merger = PdfMerger()
pdf_merger.append(file_in)

pdf_merger.add_metadata({'/Author': author, '/Title': title, '/Creator': f'ISBN: {args.isbn}'})

if 'toc' in book_info:
    print('Criando TOC...')
    for item in book_info['toc']:
        pdf_merger.add_outline_item(item['title'], int(item['cfi'].strip('/')) - 1)
else:
    print('Não criando TOC...')

_, tmpfile = tempfile.mkstemp()
pdf_merger.write(open(tmpfile, 'wb'))

romans_end = 0
for p in existing_page_files:
    if isinstance(p, str):
        romans_end += 1

if romans_end > 0:
    print('Renumerando páginas...')
    reader = pdfrw_reader(tmpfile)
    labels = PageLabels.from_pdf(reader)

    roman_labels = PageLabelScheme(
        startpage=0,
        style='none',
        prefix='Capa',
        firstpagenum=1
    )
    labels.append(roman_labels)

    roman_labels = PageLabelScheme(
        startpage=1,
        style='roman lowercase',
        firstpagenum=1
    )
    labels.append(roman_labels)

    normal_labels = PageLabelScheme(
        startpage=romans_end,
        style='arabic',
        firstpagenum=1
    )
    labels.append(normal_labels)

    labels.write(reader)
    writer = pdfrw_writer()
    writer.trailer = reader
    writer.write(args.output / f'{title}.pdf')
else:
    shutil.move(tmpfile, args.output / f'{title}.pdf')

os.remove(tmpfile)

if args.compress:
    print('Comprimindo PDF...')
    reader = PdfReader(args.output / f'{title}.pdf')
    writer = PdfWriter()
    for page in reader.pages:
        page.compress_content_streams()
        writer.add_page(page)
    with open(args.output / f'{title} compressed.pdf', 'wb') as f:
        writer.write(f)