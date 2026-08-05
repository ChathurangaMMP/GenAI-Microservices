import json
import urllib.parse
import boto3
import os
import fitz  # PyMuPDF
import re
from langdetect import detect, LangDetectException
from spellchecker import SpellChecker

# Initialize AWS clients outside the handler so they are reused across warm starts
s3_client = boto3.client('s3')

# Environment variables
DESTINATION_BUCKET = os.environ.get('DESTINATION_BUCKET', 'processed-text-bucket')

class TextQualityValidator:
    def __init__(self, expected_language='en'):
        self.expected_language = expected_language
        self.spell = SpellChecker(language=expected_language)
        
    def detect_artifacts(self, text: str) -> bool:
        if "\ufffd" in text:
            return True
        alphanumeric_count = sum(c.isalnum() for c in text)
        total_chars = len(text.replace(" ", "").replace("\n", ""))
        if total_chars > 0:
            symbol_ratio = (total_chars - alphanumeric_count) / total_chars
            if symbol_ratio > 0.20:
                return True
        return False

    def detect_foreign_language(self, text: str) -> bool:
        try:
            detected_lang = detect(text)
            return detected_lang != self.expected_language
        except LangDetectException:
            return True 

    def calculate_oov_ratio(self, text: str, threshold: float = 0.15) -> bool:
        clean_text = re.sub(r'[^\w\s]', '', text.lower())
        words = clean_text.split()
        alpha_words = [w for w in words if w.isalpha()]
        if not alpha_words:
            return False
        misspelled = self.spell.unknown(alpha_words)
        return (len(misspelled) / len(alpha_words)) > threshold

    def validate(self, extracted_text: str) -> dict:
        if not extracted_text or not extracted_text.strip():
            return {"is_valid": False, "flags": ["Empty extraction"]}
        
        flags = []
        if self.detect_foreign_language(extracted_text): 
            flags.append("Foreign language detected")
        if self.detect_artifacts(extracted_text): 
            flags.append("High symbol density or encoding artifacts")
        if self.calculate_oov_ratio(extracted_text): 
            flags.append("High Out-Of-Vocabulary ratio (OCR garble)")
            
        return {
            "is_valid": len(flags) == 0,
            "flags": flags
        }

def extract_precise_layout_with_topics(
    pdf_path: str, y_tolerance: float = 10.0, space_width: float = 5.0,
    column_gap_threshold: float = 40.0, column_gap_multiplier: int = 1
) -> str:
    doc = fitz.open(pdf_path)
    full_document_text = []

    for page_num, page in enumerate(doc):
        text_dict = page.get_text("dict")
        lines_by_y = {}
        
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0: continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text").strip()
                    if not text: continue
                    
                    bbox, font_size, font_name, flags = span.get("bbox"), span.get("size"), span.get("font", "").lower(), span.get("flags")
                    is_bold = (flags & 2**4) or ("bold" in font_name)
                    y_bucket = round(bbox[1] / y_tolerance)
                    
                    lines_by_y.setdefault(y_bucket, []).append({
                        "x0": bbox[0], "x1": bbox[2], "text": text,
                        "size": font_size, "is_bold": is_bold
                    })
        
        if not lines_by_y: continue

        sorted_y_buckets = sorted(lines_by_y.keys())
        page_strings = []
        
        for y in sorted_y_buckets:
            line_spans = lines_by_y[y]
            line_spans.sort(key=lambda s: s["x0"])
            max_font_size = max(s["size"] for s in line_spans)
            is_header = max_font_size > 14
            
            line_str = ""
            cursor_x = 0.0
            
            for s in line_spans:
                gap = s["x0"] - cursor_x
                if cursor_x == 0.0:
                    line_str += " " * max(0, int(s["x0"] / space_width))
                elif gap > 0:
                    num_spaces = max(1, int(gap / space_width))
                    if gap > column_gap_threshold:
                        num_spaces *= column_gap_multiplier
                    line_str += " " * num_spaces
                else:
                    line_str += " "
                
                if s["is_bold"] and not is_header:
                    line_str += f"**{s['text']}**"
                else:
                    line_str += s["text"]
                cursor_x = s["x1"]
            
            if is_header:
                clean_line = line_str.strip()
                if max_font_size > 20: line_str = f"# {clean_line}"
                elif max_font_size > 16: line_str = f"## {clean_line}"
                else: line_str = f"### {clean_line}"
            
            page_strings.append(line_str.rstrip())
            
        full_document_text.append(f"--- PAGE {page_num + 1} ---")
        full_document_text.append("\n".join(page_strings))

    return "\n\n".join(full_document_text)

# Initialize validator once globally
validator = TextQualityValidator()

def handler(event, context):
    """
    Triggered by SQS. The SQS message body contains the S3 event notification.
    """
    for sqs_record in event.get('Records', []):
        try:
            # SQS payload body is a JSON string containing the S3 event
            sqs_body = json.loads(sqs_record['body'])
            
            # S3 events can contain multiple records (e.g., multiple file uploads at once)
            for s3_record in sqs_body.get('Records', []):
                source_bucket = s3_record['s3']['bucket']['name']
                # Decode the key in case it has spaces or special characters
                object_key = urllib.parse.unquote_plus(s3_record['s3']['object']['key'])
                
                print(f"Processing s3://{source_bucket}/{object_key}")
                
                # Lambda has 512MB of ephemeral storage in /tmp/
                local_file_path = f"/tmp/{os.path.basename(object_key)}"
                
                # 1. Download PDF
                s3_client.download_file(source_bucket, object_key, local_file_path)
                
                # 2. Extract Text
                extracted_text = extract_precise_layout_with_topics(local_file_path)
                
                # 3. Validate Quality
                validation_result = validator.validate(extracted_text)
                
                # 4. Prepare output payload
                output_payload = {
                    "source_file": f"s3://{source_bucket}/{object_key}",
                    "validation": validation_result,
                    "text": extracted_text
                }
                
                # 5. Upload to destination bucket
                output_key = f"{object_key.replace('.pdf', '')}_extracted.json"
                s3_client.put_object(
                    Bucket=DESTINATION_BUCKET,
                    Key=output_key,
                    Body=json.dumps(output_payload),
                    ContentType='application/json'
                )
                
                print(f"Successfully processed and saved to s3://{DESTINATION_BUCKET}/{output_key}")
                
        except Exception as e:
            print(f"Error processing record: {str(e)}")
            # Raising the error tells SQS the processing failed, leaving the message in the queue to be retried
            raise e