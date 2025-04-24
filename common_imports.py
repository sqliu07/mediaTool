import os
import re
import json
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime