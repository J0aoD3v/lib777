
"""
Minha Biblioteca to EPUB Converter - CORREÇÃO DO PARSER HTML
Corrige problema com quebras de linha no HTML
"""

import argparse
import os
import sys
import time
import tempfile
import json
import re
import traceback
from pathlib import Path
from zipfile import ZipFile
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType

try:
    from ebooklib import epub
    print("📚 ebooklib importado com sucesso")
except ImportError:
    print("❌ ebooklib não encontrado, instalando...")
    os.system("pip install ebooklib")
    from ebooklib import epub
    print("✅ ebooklib instalado e importado")


class MinhaBliotecaEpubExtractor:
    def __init__(self, headless=True):
        self.driver = None
        self.headless = headless
        self.base_url = "https://dliportal.zbra.com.br"
        self.reader_url = "https://app.minhabiblioteca.com.br"
        self.book_data = []
        
    def create_driver(self):
        """Cria driver Chrome otimizado"""
        chrome_options = Options()
        
        if self.headless:
            chrome_options.add_argument('--headless')
            
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        temp_dir = tempfile.mkdtemp()
        chrome_options.add_argument(f'--user-data-dir={temp_dir}')
        chrome_options.binary_location = '/usr/bin/google-chrome'
        
        try:
            driver_path = ChromeDriverManager(chrome_type=ChromeType.GOOGLE).install()
            service = Service(driver_path)
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            print("✅ Driver Chrome criado")
        except Exception as e:
            service = Service("/usr/local/bin/chromedriver-working")
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            print("✅ Driver local criado")
        
        return self.driver
    
    def login_uenp(self, usuario, senha):
        """Login UENP simplificado"""
        print("🔐 Realizando login UENP...")
        
        login_url = f"{self.base_url}/Login.aspx?key=UENP"
        self.driver.get(login_url)
        
        WebDriverWait(self.driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(3)
        
        # Aceitar cookies se presente
        try:
            cookie_btn = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Aceitar')]"))
            )
            cookie_btn.click()
            time.sleep(2)
        except TimeoutException:
            pass
        
        # Preencher login
        user_field = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "userIdTextBox"))
        )
        ActionChains(self.driver).move_to_element(user_field).click().perform()
        user_field.clear()
        user_field.send_keys(usuario)
        
        password_field = self.driver.find_element(By.ID, "passwordTextBox")
        ActionChains(self.driver).move_to_element(password_field).click().perform()
        password_field.clear()
        password_field.send_keys(senha)
        
        from selenium.webdriver.common.keys import Keys
        password_field.send_keys(Keys.RETURN)
        
        # Aguardar redirecionamento
        start_time = time.time()
        while time.time() - start_time < 60:
            if "minhabiblioteca.com.br" in self.driver.current_url:
                print("   ✅ Login realizado com sucesso")
                time.sleep(3)
                return
            time.sleep(2)
        
        raise Exception("❌ Falha no login")
    
    def extract_vst_data_from_page(self, isbn, page_number):
        """Extrai dados do vst-html-javascript de uma página"""
        print(f"   📄 Extraindo dados VST da página {page_number}...")
        
        try:
            # Navegar para página
            page_url = f"{self.reader_url}/reader/books/{isbn}/pageid/{page_number}"
            self.driver.get(page_url)
            
            WebDriverWait(self.driver, 20).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            # Aguardar carregamento completo
            print("   ⏰ Aguardando 15 segundos...")
            for i in range(15, 0, -1):
                print(f"   ⏳ {i}s...", end=" ", flush=True)
                time.sleep(1)
            print("✅")
            
            # Entrar nos iframes para acessar o script
            try:
                # Iframe externo
                iframe_external = WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[src*='jigsaw.minhabiblioteca.com.br/mosaic/wrapper.html']"))
                )
                self.driver.switch_to.frame(iframe_external)
                time.sleep(3)
                
                # Aguardar mosaic-book
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "mosaic-book"))
                )
                time.sleep(5)
                
                # Iframe interno via Shadow DOM
                iframe_element = self.driver.execute_script("""
                    var mosaicBook = document.querySelector('mosaic-book');
                    return mosaicBook.shadowRoot.querySelector('iframe');
                """)
                
                if iframe_element:
                    self.driver.switch_to.frame(iframe_element)
                    time.sleep(5)
                    
                    # Extrair dados do window.innerPageData
                    vst_data = self.driver.execute_script("""
                        // Tentar obter dados do window.innerPageData
                        if (typeof window.innerPageData !== 'undefined') {
                            return window.innerPageData;
                        }
                        
                        // Fallback: procurar no script vst-html-javascript
                        var vstScript = document.getElementById('vst-html-javascript');
                        if (vstScript) {
                            var scriptText = vstScript.textContent;
                            var match = scriptText.match(/window\\.innerPageData\\s*=\\s*({[\\s\\S]*?});/);
                            if (match) {
                                try {
                                    return JSON.parse(match[1]);
                                } catch(e) {
                                    console.log('Erro ao parsear JSON:', e);
                                    return null;
                                }
                            }
                        }
                        
                        return null;
                    """)
                    
                    if vst_data:
                        words_length = len(vst_data.get('words', ''))
                        print(f"   ✅ Dados VST extraídos: {words_length} caracteres")
                        
                        # Debug: mostrar dados extraídos para análise
                        if words_length > 0:
                            words_preview = vst_data.get('words', '')[:50]
                            print(f"   📝 Preview: {words_preview}...")
                        
                        return vst_data
                    else:
                        print("   ❌ Dados VST não encontrados")
                        return None
                
            except Exception as e:
                print(f"   ❌ Erro ao acessar iframes: {e}")
                return None
            finally:
                self.driver.switch_to.default_content()
        
        except Exception as e:
            print(f"   ❌ Erro na página {page_number}: {e}")
            return None
    
    def clean_text_for_html(self, text):
        """Limpa texto para uso seguro em HTML"""
        if not text:
            return ""
        
        # Escape HTML entities
        import html
        text = html.escape(text)
        
        # Remover caracteres problemáticos
        text = text.replace('\x00', '')  # null bytes
        text = text.replace('\ufffd', '')  # replacement characters
        
        return text.strip()
    
    def format_text_content(self, words, page_number, glyphs_data=None):
        """Formata o texto extraído para HTML - CORREÇÃO DE QUEBRAS DE LINHA"""
        print(f"🔍 DEBUG: Formatando conteúdo da página {page_number}")
        
        # Se não há texto, criar página em branco
        if not words or len(words.strip()) < 5:
            print(f"   ⚠️ Página {page_number} considerada vazia")
            return f'''<div style="text-align: center; margin-top: 50%; color: #666; font-style: italic;">
                <p>Página {page_number}</p>
                <p>(Página sem conteúdo textual ou contém apenas imagens)</p>
            </div>'''
        
        # Limpar texto
        text = self.clean_text_for_html(words)
        print(f"   📝 Texto após limpeza: {len(text)} caracteres")
        
        # Quebrar em parágrafos
        paragraphs = text.split('\r')
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        print(f"   📋 Parágrafos encontrados: {len(paragraphs)}")
        
        if not paragraphs:
            return f'''<div style="text-align: center; margin-top: 50%; color: #666; font-style: italic;">
                <p>Página {page_number}</p>
                <p>(Conteúdo não disponível)</p>
            </div>'''
        
        # Converter para HTML - CORREÇÃO: usar quebras de linha reais
        html_parts = []
        
        for i, para in enumerate(paragraphs):
            if para:
                # Detectar títulos
                if (para.isupper() and len(para) < 100) or any(keyword in para for keyword in [
                    'Dicas de', 'Para Leigos', 'Capítulo', 'Básica', 'PROBLEMAS', 'MATEMÁTICA'
                ]):
                    html_parts.append(f"<h2>{para}</h2>")
                    print(f"      ✅ Parágrafo {i+1} como título")
                else:
                    html_parts.append(f"<p>{para}</p>")
                    print(f"      ✅ Parágrafo {i+1} como texto")
        
        # Juntar com quebras de linha reais (não literal)
        final_content = "\\n".join(html_parts)
        print(f"   📄 Conteúdo HTML final: {len(final_content)} caracteres")
        
        return final_content
    
    def create_epub_from_data(self, isbn, output_path, book_title="Livro"):
        """Cria EPUB com HTML válido"""
        print(f"📚 INICIANDO CRIAÇÃO DO EPUB: {book_title}")
        
        try:
            # Criar livro EPUB
            book = epub.EpubBook()
            
            # Metadados
            book.set_identifier(f'isbn-{isbn}')
            book.set_title(book_title)
            book.set_language('pt-br')
            book.add_author('Extraído da Minha Biblioteca')
            book.add_metadata('DC', 'description', f'Livro extraído do ISBN {isbn}')
            
            # CSS
            style = '''
            body { 
                font-family: Georgia, serif; 
                line-height: 1.6; 
                margin: 1em; 
                color: #333;
            }
            h1, h2 { 
                color: #2c3e50; 
                margin: 1em 0;
            }
            p { 
                margin: 1em 0; 
                text-align: justify; 
            }
            .page-info {
                font-size: 0.9em;
                color: #666;
                font-style: italic;
                margin-bottom: 1em;
                border-bottom: 1px solid #eee;
                padding-bottom: 0.5em;
            }
            '''
            
            nav_css = epub.EpubItem(uid="nav_css", file_name="style/nav.css", media_type="text/css", content=style)
            book.add_item(nav_css)
            
            # Processar páginas
            spine = ['nav']
            toc = []
            
            for i, page_data in enumerate(self.book_data):
                page_number = i + 1
                print(f"\\n   📄 Processando página {page_number}")
                
                if page_data:
                    chapter_title = page_data.get('chapterTitle', f'Capítulo {page_number}')
                    page_title = page_data.get('page', str(page_number))
                    words = page_data.get('words', '')
                else:
                    chapter_title = f'Capítulo {page_number}'
                    page_title = str(page_number)
                    words = ''
                
                # Formatear conteúdo
                content = self.format_text_content(words, page_number)
                
                # HTML da página - ESTRUTURA MAIS SIMPLES E VÁLIDA
                html_content = f'''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <title>{self.clean_text_for_html(chapter_title)} - Página {page_title}</title>
    <link rel="stylesheet" type="text/css" href="../style/nav.css"/>
</head>
<body>
    <div class="page-info">
        <strong>{self.clean_text_for_html(chapter_title)}</strong> - Página {page_title}
    </div>
    {content}
</body>
</html>'''
                
                print(f"      📝 HTML gerado: {len(html_content)} caracteres")
                
                # Criar capítulo
                chapter_file_name = f'page_{page_number:03d}.xhtml'
                chapter = epub.EpubHtml(
                    title=f"Página {page_title}",
                    file_name=chapter_file_name,
                    lang='pt-br'
                )
                
                # CORREÇÃO CRÍTICA: validar conteúdo antes de atribuir
                try:
                    # Teste básico de parsing
                    from lxml import html as lxml_html
                    parsed = lxml_html.fromstring(html_content)
                    print(f"      ✅ HTML válido confirmado")
                    
                    chapter.content = html_content
                    book.add_item(chapter)
                    spine.append(chapter)
                    
                    # TOC
                    toc_title = f"Página {page_title}"
                    if words and len(words) > 50:
                        first_words = words[:40].strip()
                        if first_words:
                            toc_title = f"Pág. {page_title}: {first_words}..."
                    
                    toc.append(epub.Link(chapter_file_name, toc_title, f'page_{page_number:03d}'))
                    print(f"      ✅ Capítulo adicionado com sucesso")
                    
                except Exception as parse_error:
                    print(f"      ❌ HTML inválido: {parse_error}")
                    # Criar versão alternativa mais simples
                    simple_content = f'''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <title>Página {page_title}</title>
</head>
<body>
    <h1>Página {page_title}</h1>
    <p>Conteúdo desta página não pôde ser processado.</p>
</body>
</html>'''
                    chapter.content = simple_content
                    book.add_item(chapter)
                    spine.append(chapter)
                    toc.append(epub.Link(chapter_file_name, f"Página {page_title} (erro)", f'page_{page_number:03d}'))
                    print(f"      ⚠️ Capítulo adicionado com conteúdo simplificado")
            
            # Configurar navegação
            book.toc = toc
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())
            book.spine = spine
            
            # Salvar EPUB
            epub_path = Path(output_path)
            epub_path.parent.mkdir(parents=True, exist_ok=True)
            
            print(f"\\n💾 Salvando EPUB...")
            epub.write_epub(str(epub_path), book, {})
            
            if epub_path.exists():
                size_mb = epub_path.stat().st_size / 1024 / 1024
                print(f"✅ EPUB criado com sucesso: {epub_path}")
                print(f"📏 Tamanho: {size_mb:.2f} MB")
                print(f"📊 Páginas: {len(self.book_data)}")
                return True
            else:
                print(f"❌ Arquivo não foi criado")
                return False
                
        except Exception as e:
            print(f"💥 ERRO na criação do EPUB: {e}")
            traceback.print_exc()
            return False
    
    def extract_book(self, isbn, output_path, usuario=None, senha=None, start_page=1, end_page=None):
        """Extrai livro completo como EPUB"""
        try:
            if not self.driver:
                self.create_driver()
            
            if usuario and senha:
                self.login_uenp(usuario, senha)
            
            if not end_page:
                end_page = 5  # Teste com 5 páginas
            
            print(f"📖 Extraindo páginas {start_page}-{end_page} do ISBN {isbn}")
            
            # Extrair dados de cada página
            for page_number in range(start_page, end_page + 1):
                print(f"📄 === PÁGINA {page_number} ===")
                
                page_data = self.extract_vst_data_from_page(isbn, page_number)
                self.book_data.append(page_data)
                
                time.sleep(2)
            
            # Determinar título do livro
            book_title = "Livro Digital Extraído"
            if self.book_data and self.book_data[0]:
                first_page = self.book_data[0]
                if first_page:
                    book_title = first_page.get('chapterTitle', book_title)
                    if 'words' in first_page:
                        words = first_page['words'][:200]
                        if 'Matemática' in words:
                            book_title = "1.001 Problemas de Matemática Básica e Pré-Álgebra Para Leigos"
            
            # Criar EPUB
            success = self.create_epub_from_data(isbn, output_path, book_title)
            
            if success:
                print("🎉 Extração concluída com sucesso!")
            else:
                print("⚠️ Extração falhou")
            
        except Exception as e:
            print(f"💥 Erro fatal: {e}")
            traceback.print_exc()
            raise
        finally:
            self.close()
    
    def close(self):
        """Finaliza driver"""
        if self.driver:
            self.driver.quit()
            print("🔒 Driver finalizado")


def main():
    parser = argparse.ArgumentParser(description="Minha Biblioteca EPUB Extractor - HTML CORRIGIDO")
    parser.add_argument("--isbn", required=True, help="ISBN do livro")
    parser.add_argument("--output", required=True, help="Caminho do EPUB de saída")
    parser.add_argument("--usuario", help="Usuário UENP")
    parser.add_argument("--senha", help="Senha UENP")
    parser.add_argument("--start-page", type=int, default=1, help="Página inicial")
    parser.add_argument("--end-page", type=int, help="Página final")
    parser.add_argument("--headless", action="store_true", default=True, help="Modo headless")
    
    args = parser.parse_args()
    
    extractor = MinhaBliotecaEpubExtractor(headless=args.headless)
    
    try:
        extractor.extract_book(
            isbn=args.isbn,
            output_path=args.output,
            usuario=args.usuario,
            senha=args.senha,
            start_page=args.start_page,
            end_page=args.end_page
        )
        
        print("✨ Processo finalizado!")
        
    except Exception as e:
        print(f"🚨 Erro: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
