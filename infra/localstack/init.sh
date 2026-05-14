#!/bin/bash
set -e
echo "Initializing LocalStack resources..."
awslocal s3 mb s3://trustline-compliance --region us-east-1
echo "S3 bucket trustline-compliance created."
