from django.shortcuts import render
from .models import Product

# Create your views here.

def index(request):
    products = Product.objects.all()
    return render(request, 'mandii/index.html',{'products':products})

def detail(request, id):
    product = Product.objects.get(id=id)
    return render(request, 'mandii/detail.html',{'product':product})