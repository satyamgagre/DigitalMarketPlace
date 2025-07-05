from django.contrib import admin
from django.urls import path
from . import views
from django.conf.urls.static import static
from django.conf import settings

urlpatterns = [
    path('', views.index, name='index'),
    path('product/<int:id>', views.detail, name='detail'),
    
    path('create-checkout-session/<int:id>/', views.create_checkout_session, name='create_checkout_session'),
    path('payment-verification/', views.payment_verification, name='payment_verification'),
    path('payment-success/<int:order_id>/', views.payment_success_view, name='payment_success'),
    path('payment-failed/', views.payment_failed_view, name='payment_failed'),
    path('api/checkout-session/<int:id>/', views.create_checkout_session, name='api_checkout_session'),
    # path('razorpay-webhook/', views.razorpay_webhook, name='razorpay_webhook'),
]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)