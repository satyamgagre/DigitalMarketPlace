from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from .models import Product, OrderDetail
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import razorpay
from django.http import JsonResponse
import json
import logging

# Set up logging
logger = logging.getLogger(__name__)

def index(request):
    products = Product.objects.filter(is_active=True)
    return render(request, 'mandii/index.html', {'products': products})

def detail(request, id):
    product = get_object_or_404(Product, id=id, is_active=True)
    razorpay_key_id = settings.RAZORPAY_KEY_ID
    return render(request, 'mandii/detail.html', {
        'product': product, 
        'razorpay_key_id': razorpay_key_id
    })

@csrf_exempt
@require_POST
def create_checkout_session(request, id):
    try:
        product = get_object_or_404(Product, id=id, is_active=True)
        
        # Read customer email from frontend JSON body
        try:
            data = json.loads(request.body)
            customer_email = data.get("email")
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
        
        if not customer_email:
            return JsonResponse({'error': 'Email is required'}, status=400)
        
        # Validate email format (basic check)
        if '@' not in customer_email:
            return JsonResponse({'error': 'Invalid email format'}, status=400)

        # Razorpay client initialization
        try:
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        except Exception as e:
            logger.error(f"Razorpay client initialization failed: {str(e)}")
            return JsonResponse({'error': 'Payment service unavailable'}, status=500)

        # Order data
        amount = int(product.price * 100)  # Convert to paise
        order_data = {
            "amount": amount,
            "currency": "INR",
            "receipt": f"order_rcptid_{id}_{customer_email}",
            "payment_capture": 1
        }

        try:
            order = client.order.create(data=order_data)
        except razorpay.errors.RazorpayError as e:
            logger.error(f"Razorpay order creation failed: {str(e)}")
            return JsonResponse({'error': 'Failed to create payment order'}, status=500)

        # Save in DB
        try:
            order_detail = OrderDetail.objects.create(
                customer_email=customer_email,
                product=product,
                amount=amount,
                razorpay_order_id=order['id'],
                payment_status='pending'
            )
            logger.info(f"Order created: {order_detail.id} for {customer_email}")
        except Exception as e:
            logger.error(f"Database save failed: {str(e)}")
            return JsonResponse({'error': 'Failed to save order details'}, status=500)

        return JsonResponse({
            "order_id": order['id'],
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "amount": amount,
            "currency": "INR",
            "product_name": product.name,
            "customer_email": customer_email
        })

    except Exception as e:
        logger.error(f"Unexpected error in create_checkout_session: {str(e)}")
        return JsonResponse({'error': 'An unexpected error occurred'}, status=500)

@csrf_exempt
@require_POST
def payment_verification(request):
    """Verify payment after successful payment"""
    try:
        data = json.loads(request.body)
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_signature = data.get('razorpay_signature')
        
        if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
            return JsonResponse({'error': 'Missing required payment data'}, status=400)
        
        # Get order from database
        try:
            order = OrderDetail.objects.get(razorpay_order_id=razorpay_order_id)
        except OrderDetail.DoesNotExist:
            return JsonResponse({'error': 'Order not found'}, status=404)
        
        # Verify payment signature
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        
        try:
            client.utility.verify_payment_signature({
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            })
        except razorpay.errors.SignatureVerificationError:
            logger.error(f"Payment signature verification failed for order {razorpay_order_id}")
            return JsonResponse({'error': 'Payment verification failed'}, status=400)
        
        # Update order status
        order.razorpay_payment_id = razorpay_payment_id
        order.razorpay_signature = razorpay_signature
        order.has_paid = True
        order.payment_status = 'completed'
        order.save()
        
        logger.info(f"Payment verified successfully for order {order.id}")
        return JsonResponse({
            'status': 'success',
            'order_id': order.id,
            'redirect_url': f'/payment-success/{order.id}/'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        logger.error(f"Payment verification error: {str(e)}")
        return JsonResponse({'error': 'Payment verification failed'}, status=500)

def payment_success_view(request, order_id):
    """Display payment success page"""
    try:
        order = get_object_or_404(OrderDetail, id=order_id, has_paid=True)
        
        context = {
            'order': order,
            'product': order.product,
            'amount_paid': order.amount_in_rupees,
            'order_id': order.id,
            'transaction_id': order.razorpay_payment_id,
            'customer_email': order.customer_email,
        }
        
        return render(request, 'mandii/payment_success.html', context)
        
    except Exception as e:
        logger.error(f"Error in payment_success_view: {str(e)}")
        messages.error(request, 'Order not found or payment not completed.')
        return redirect('index')

def payment_failed_view(request):
    """Display payment failure page"""
    # Get failure details from session or query parameters
    razorpay_order_id = request.GET.get('order_id')
    error_code = request.GET.get('error_code')
    error_description = request.GET.get('error_description')
    
    order = None
    if razorpay_order_id:
        try:
            order = OrderDetail.objects.get(razorpay_order_id=razorpay_order_id)
            # Update order status to failed
            order.payment_status = 'failed'
            order.failure_reason = f"Error: {error_code} - {error_description}"
            order.save()
            
            logger.info(f"Payment failed for order {order.id}: {error_description}")
        except OrderDetail.DoesNotExist:
            logger.error(f"Order not found for failed payment: {razorpay_order_id}")
    
    context = {
        'order': order,
        'error_code': error_code,
        'error_description': error_description or 'Payment was cancelled or failed',
        'razorpay_order_id': razorpay_order_id,
    }
    
    return render(request, 'mandii/payment_failed.html', context)

@csrf_exempt
def razorpay_webhook(request):
    """Handle Razorpay webhooks for payment events"""
    if request.method == 'POST':
        try:
            webhook_signature = request.META.get('HTTP_X_RAZORPAY_SIGNATURE')
            webhook_body = request.body
            
            if not webhook_signature:
                return JsonResponse({'error': 'Missing webhook signature'}, status=400)
            
            # Verify webhook signature (if webhook secret is configured)
            if hasattr(settings, 'RAZORPAY_WEBHOOK_SECRET') and settings.RAZORPAY_WEBHOOK_SECRET:
                client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
                try:
                    client.utility.verify_webhook_signature(
                        webhook_body, 
                        webhook_signature, 
                        settings.RAZORPAY_WEBHOOK_SECRET
                    )
                except razorpay.errors.SignatureVerificationError:
                    logger.error("Webhook signature verification failed")
                    return JsonResponse({'error': 'Invalid webhook signature'}, status=400)
            else:
                logger.warning("Webhook secret not configured - skipping signature verification")
            
            # Process webhook data
            data = json.loads(webhook_body)
            event = data.get('event')
            payload = data.get('payload', {})
            
            if event == 'payment.captured':
                # Handle successful payment
                payment_entity = payload.get('payment', {}).get('entity', {})
                order_id = payment_entity.get('order_id')
                
                if order_id:
                    try:
                        order = OrderDetail.objects.get(razorpay_order_id=order_id)
                        if not order.has_paid:
                            order.razorpay_payment_id = payment_entity.get('id')
                            order.has_paid = True
                            order.payment_status = 'completed'
                            order.save()
                            logger.info(f"Payment captured via webhook for order {order.id}")
                    except OrderDetail.DoesNotExist:
                        logger.error(f"Order not found for webhook payment: {order_id}")
            
            elif event == 'payment.failed':
                # Handle failed payment
                payment_entity = payload.get('payment', {}).get('entity', {})
                order_id = payment_entity.get('order_id')
                
                if order_id:
                    try:
                        order = OrderDetail.objects.get(razorpay_order_id=order_id)
                        order.payment_status = 'failed'
                        order.failure_reason = payment_entity.get('error_description', 'Payment failed')
                        order.save()
                        logger.info(f"Payment failed via webhook for order {order.id}")
                    except OrderDetail.DoesNotExist:
                        logger.error(f"Order not found for webhook payment failure: {order_id}")
            
            return JsonResponse({'status': 'success'})
            
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
        except Exception as e:
            logger.error(f"Webhook processing error: {str(e)}")
            return JsonResponse({'error': 'Webhook processing failed'}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)