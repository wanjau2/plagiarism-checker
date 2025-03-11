from flask import Flask, render_template, request, jsonify, send_file
import os
import random
import re
import requests
from bs4 import BeautifulSoup
import nltk
from nltk.tokenize import sent_tokenize
import json
from io import BytesIO
from urllib.parse import quote_plus
from difflib import SequenceMatcher
import time

# Initialize Flask application
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads/'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

# Ensure the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Download necessary NLTK data
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')
    nltk.download('punkt_tab')

# Handle docx file import conditionally to avoid errors
try:
    import docx
    docx_available = True
except ImportError:
    docx_available = False
    print("Warning: python-docx module not available. DOCX parsing will fall back to mammoth.")

# Handle mammoth import
try:
    import mammoth
    mammoth_available = True
except ImportError:
    mammoth_available = False
    print("Warning: mammoth module not available. Some document formats may not be supported.")

def extract_text_from_docx(file_path):
    """Extract text from a DOCX file."""
    if docx_available:
        try:
            doc = docx.Document(file_path)
            return " ".join([paragraph.text for paragraph in doc.paragraphs])
        except Exception as e:
            print(f"Error using python-docx: {e}")
    
    # Fallback to mammoth if python-docx fails or is not available
    if mammoth_available:
        try:
            with open(file_path, "rb") as docx_file:
                result = mammoth.extract_raw_text(docx_file)
                return result.value
        except Exception as e:
            print(f"Error using mammoth: {e}")
            
    # Last resort fallback
    try:
        from zipfile import ZipFile
        from xml.etree.ElementTree import XML
        
        WORD_NAMESPACE = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        PARA = WORD_NAMESPACE + 'p'
        TEXT = WORD_NAMESPACE + 't'
        
        with ZipFile(file_path) as docx_file:
            with docx_file.open('word/document.xml') as content:
                tree = XML(content.read())
                paragraphs = []
                for paragraph in tree.iter(PARA):
                    texts = [node.text for node in paragraph.iter(TEXT) if node.text]
                    if texts:
                        paragraphs.append(''.join(texts))
                return '\n\n'.join(paragraphs)
    except Exception as e:
        print(f"Error using fallback DOCX method: {e}")
        return "Error: Could not extract text from DOCX file."

def extract_text_from_txt(file_path):
    """Extract text from a TXT file."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
            return file.read()
    except Exception as e:
        print(f"Error reading text file: {e}")
        return "Error: Could not read text file."

def extract_random_phrases(text, num_phrases=5, phrase_length=8):
    """Extract random phrases from text for Google searches."""
    sentences = sent_tokenize(text)
    
    # Clean sentences and remove very short ones
    sentences = [re.sub(r'\s+', ' ', s).strip() for s in sentences if len(s.split()) > phrase_length]
    
    if not sentences:
        return []
    
    phrases = []
    for _ in range(min(num_phrases, len(sentences))):
        # Get a random sentence
        sentence = random.choice(sentences)
        words = sentence.split()
        
        if len(words) <= phrase_length:
            phrases.append(sentence)
        else:
            # Extract a random phrase of specified length
            start_index = random.randint(0, len(words) - phrase_length)
            phrase = " ".join(words[start_index:start_index + phrase_length])
            phrases.append(phrase)
    
    return phrases

def search_google(query, api_key=None):
    """
    Search Google for a query and return the top 5 results.
    Uses a custom Google Search API or direct HTML scraping as fallback.
    """
    # For demonstration purposes, we're using a mock function
    # In production, use Google Custom Search API or ScrapingBee
    search_url = f"https://www.google.com/search?q={quote_plus(query)}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        search_results = []
        # This selector would need to be updated based on Google's current HTML structure
        for result in soup.select("div.g")[:5]:
            link_elem = result.select_one("a")
            if link_elem and 'href' in link_elem.attrs:
                url = link_elem['href']
                if url.startswith('/url?q='):
                    url = url.split('/url?q=')[1].split('&')[0]
                    
                if url.startswith('http'):
                    search_results.append(url)
        
        return search_results
    except Exception as e:
        print(f"Error searching Google: {e}")
        return []

def scrape_content(url):
    """Scrape and clean content from a URL."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove script, style elements and comments
        for element in soup(["script", "style", "header", "footer", "nav"]):
            element.decompose()
        
        # Get the main content (this is a simplified approach)
        # A more sophisticated approach would use site-specific selectors
        main_content = soup.find('main') or soup.find('article') or soup.find('div', {'id': 'content'}) or soup.body
        
        if main_content:
            text = main_content.get_text(separator=' ', strip=True)
        else:
            text = soup.get_text(separator=' ', strip=True)
        
        # Clean the text
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return ""

def calculate_similarity(text1, text2):
    """Calculate similarity between two texts using SequenceMatcher."""
    return SequenceMatcher(None, text1, text2).ratio()

def find_similar_passages(original_text, scraped_text, min_length=40, min_similarity=0.8):
    """Find similar passages between the original and scraped text."""
    original_sentences = sent_tokenize(original_text)
    scraped_sentences = sent_tokenize(scraped_text)
    
    similar_passages = []
    
    for orig_sentence in original_sentences:
        if len(orig_sentence) < min_length:
            continue
            
        for scraped_sentence in scraped_sentences:
            if len(scraped_sentence) < min_length:
                continue
                
            similarity = calculate_similarity(orig_sentence, scraped_sentence)
            if similarity > min_similarity:
                similar_passages.append({
                    'original': orig_sentence,
                    'scraped': scraped_sentence,
                    'similarity': similarity
                })
    
    return similar_passages

def generate_pdf_report(results, output_file="plagiarism_report.pdf"):
    """Generate a PDF report of plagiarism detection results."""
    # Import reportlab libraries here to handle potential import errors
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []
        
        # Add title
        title_style = styles['Title']
        story.append(Paragraph("Plagiarism Detection Report", title_style))
        story.append(Spacer(1, 12))
        
        # Add summary
        overall_similarity = results['overall_similarity']
        story.append(Paragraph(f"Overall Similarity: {overall_similarity:.2%}", styles['Heading2']))
        story.append(Spacer(1, 12))
        
        # Add details for each phrase
        for phrase_result in results['phrase_results']:
            phrase = phrase_result['phrase']
            story.append(Paragraph(f"Search Phrase: \"{phrase}\"", styles['Heading3']))
            story.append(Spacer(1, 6))
            
            for url_result in phrase_result['url_results']:
                url = url_result['url']
                similarity = url_result['similarity']
                story.append(Paragraph(f"Source: {url}", styles['Normal']))
                story.append(Paragraph(f"Similarity: {similarity:.2%}", styles['Normal']))
                story.append(Spacer(1, 6))
                
                # Add similar passages table
                if url_result['similar_passages']:
                    data = [["Original Text", "Similar Text", "Similarity"]]
                    for passage in url_result['similar_passages'][:5]:  # Limit to top 5 for readability
                        data.append([
                            passage['original'][:100] + "..." if len(passage['original']) > 100 else passage['original'],
                            passage['scraped'][:100] + "..." if len(passage['scraped']) > 100 else passage['scraped'],
                            f"{passage['similarity']:.2%}"
                        ])
                    
                    table = Table(data, colWidths=[doc.width/3.0]*3)
                    table.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                        ('GRID', (0, 0), (-1, -1), 1, colors.black)
                    ]))
                    story.append(table)
                
                story.append(Spacer(1, 12))
        
        doc.build(story)
        buffer.seek(0)
        return buffer
    except ImportError as e:
        print(f"Error importing ReportLab for PDF generation: {e}")
        # Create a simple text buffer as fallback
        buffer = BytesIO()
        buffer.write(b"PLAGIARISM DETECTION REPORT\n\n")
        buffer.write(f"Overall Similarity: {results['overall_similarity']:.2%}\n\n".encode('utf-8'))
        
        for phrase_result in results['phrase_results']:
            buffer.write(f"Search Phrase: \"{phrase_result['phrase']}\"\n".encode('utf-8'))
            
            for url_result in phrase_result['url_results']:
                buffer.write(f"Source: {url_result['url']}\n".encode('utf-8'))
                buffer.write(f"Similarity: {url_result['similarity']:.2%}\n".encode('utf-8'))
                
                if url_result['similar_passages']:
                    buffer.write(b"Similar Passages:\n")
                    for passage in url_result['similar_passages'][:5]:
                        buffer.write(f"  Original: {passage['original'][:100]}...\n".encode('utf-8'))
                        buffer.write(f"  Similar: {passage['scraped'][:100]}...\n".encode('utf-8'))
                        buffer.write(f"  Match: {passage['similarity']:.2%}\n\n".encode('utf-8'))
            
            buffer.write(b"\n")
        
        buffer.seek(0)
        return buffer

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(file_path)
    
    # Initialize result structure
    result_id = str(int(time.time()))
    
    # Process asynchronously and return job ID
    # In a production environment, use Celery or similar for background processing
    return jsonify({
        'message': 'File uploaded successfully',
        'result_id': result_id,
        'filename': file.filename
    })

@app.route('/check_plagiarism', methods=['POST'])
def check_plagiarism():
    data = request.json
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], data['filename'])
    
    # Extract text based on file type
    if file_path.endswith('.docx'):
        original_text = extract_text_from_docx(file_path)
    else:
        original_text = extract_text_from_txt(file_path)
    
    # Extract random phrases
    phrases = extract_random_phrases(original_text, num_phrases=3, phrase_length=8)
    
    results = {
        'original_text_sample': original_text[:500] + "...",
        'overall_similarity': 0.0,
        'phrase_results': []
    }
    
    total_similarity = 0
    total_sources = 0
    
    # Process each phrase
    for phrase in phrases:
        phrase_result = {
            'phrase': phrase,
            'url_results': []
        }
        
        # Search Google for the phrase
        search_results = search_google(phrase)
        
        # Process each search result
        for url in search_results:
            scraped_text = scrape_content(url)
            
            if not scraped_text:
                continue
                
            # Calculate overall similarity
            similarity = calculate_similarity(original_text, scraped_text)
            
            # Find similar passages
            similar_passages = find_similar_passages(original_text, scraped_text)
            
            url_result = {
                'url': url,
                'similarity': similarity,
                'similar_passages': similar_passages
            }
            
            phrase_result['url_results'].append(url_result)
            total_similarity += similarity
            total_sources += 1
    
        results['phrase_results'].append(phrase_result)
    
    # Calculate overall similarity as average
    if total_sources > 0:
        results['overall_similarity'] = total_similarity / total_sources
    
    # Generate PDF report
    pdf_buffer = generate_pdf_report(results)
    
    # Save the PDF to a file
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"report_{data['result_id']}.pdf")
    with open(pdf_path, 'wb') as f:
        f.write(pdf_buffer.getvalue())
    
    return jsonify({
        'results': results,
        'pdf_url': f"/download_report/{data['result_id']}"
    })

@app.route('/download_report/<result_id>')
def download_report(result_id):
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"report_{result_id}.pdf")
    return send_file(pdf_path, as_attachment=True, download_name="plagiarism_report.pdf")

if __name__ == '__main__':
    app.run(debug=True)