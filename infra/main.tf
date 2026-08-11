terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

resource "aws_s3_bucket" "upload_bucket" {
  bucket = "pdf-upload-bucket"
}

resource "aws_s3_bucket" "processed_bucket" {
  bucket = "processed-text-bucket"
}

resource "aws_sqs_queue" "pdf_processing_queue" {
  name                       = "pdf-processing-queue"
  visibility_timeout_seconds = 300
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket = aws_s3_bucket.upload_bucket.id
  queue {
    queue_arn     = aws_sqs_queue.pdf_processing_queue.arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".pdf"
  }
}

resource "aws_iam_role" "lambda_exec" {
  name = "lambda_sqs_s3_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_lambda_function" "pdf_extractor" {
  function_name    = "pdf-extractor-worker"
  role             = aws_iam_role.lambda_exec.arn
  runtime          = "python3.11"
  
  # UPDATE: Point to the new filename (without the .py extension)
  handler          = "pdf_extraction_lambda.handler" 
  
  timeout          = 300
  filename         = "../deployment_package.zip"
  source_code_hash = filebase64sha256("../deployment_package.zip")

  environment {
    variables = {
      DESTINATION_BUCKET = aws_s3_bucket.processed_bucket.bucket
    }
  }
}

resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.pdf_processing_queue.arn
  function_name    = aws_lambda_function.pdf_extractor.arn
  batch_size       = 1
}