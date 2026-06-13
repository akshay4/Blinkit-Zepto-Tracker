import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:provider/provider.dart';
import '../services/api_service.dart';
import '../models/product.dart';

class ProductsScreen extends StatefulWidget {
  const ProductsScreen({super.key});

  @override
  State<ProductsScreen> createState() => _ProductsScreenState();
}

class _ProductsScreenState extends State<ProductsScreen> with SingleTickerProviderStateMixin {
  late TabController _tabController;
  final Map<String, bool> _checkingItems = {}; // track which URLs are currently loading instant recheck

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> _handleRefresh() async {
    final apiService = Provider.of<ApiService>(context, listen: false);
    await Future.wait([
      apiService.fetchProducts('blinkit'),
      apiService.fetchProducts('zepto'),
    ]);
  }

  Future<void> _recheckStock(ApiService api, String provider, String url) async {
    setState(() {
      _checkingItems[url] = true;
    });

    final newStatus = await api.checkProductStockInstantly(provider, url);
    
    if (mounted) {
      setState(() {
        _checkingItems[url] = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Live Stock Check Finished: $newStatus'),
          duration: const Duration(seconds: 2),
        ),
      );
    }
  }

  Future<void> _deleteProduct(ApiService api, String provider, String url, String name) async {
    final success = await api.removeProduct(provider, url);
    if (success && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Removed "$name" from tracking'),
          action: SnackBarAction(
            label: 'Undo',
            onPressed: () {
              api.addProduct(provider, name, url);
            },
          ),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final apiService = Provider.of<ApiService>(context);

    return Scaffold(
      backgroundColor: const Color(0xFFF8F9FA),
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0,
        title: Text(
          'Tracked Products',
          style: GoogleFonts.outfit(
            fontSize: 22,
            fontWeight: FontWeight.bold,
            color: const Color(0xFF1E293B),
          ),
        ),
        bottom: TabBar(
          controller: _tabController,
          labelColor: const Color(0xFF6C63FF),
          unselectedLabelColor: const Color(0xFF64748B),
          indicatorColor: const Color(0xFF6C63FF),
          indicatorWeight: 3,
          labelStyle: GoogleFonts.outfit(fontWeight: FontWeight.bold, fontSize: 15),
          unselectedLabelStyle: GoogleFonts.outfit(fontWeight: FontWeight.w500, fontSize: 15),
          tabs: const [
            Tab(text: 'Blinkit'),
            Tab(text: 'Zepto'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: [
          _buildProductsList(apiService, 'blinkit', apiService.blinkitProducts),
          _buildProductsList(apiService, 'zepto', apiService.zeptoProducts),
        ],
      ),
    );
  }

  Widget _buildProductsList(ApiService api, String provider, List<Product> products) {
    if (products.isEmpty) {
      return RefreshIndicator(
        onRefresh: _handleRefresh,
        color: const Color(0xFF6C63FF),
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          child: Container(
            height: MediaQuery.of(context).size.height * 0.6,
            alignment: Alignment.center,
            padding: const EdgeInsets.all(32.0),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: const BoxDecoration(
                    color: Color(0xFFEEF2F6),
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(Icons.shopping_basket_outlined, color: Color(0xFF64748B), size: 48),
                ),
                const SizedBox(height: 16),
                Text(
                  'No tracked products for ${provider.toUpperCase()}',
                  style: GoogleFonts.outfit(fontSize: 16, fontWeight: FontWeight.bold, color: const Color(0xFF334155)),
                ),
                const SizedBox(height: 6),
                Text(
                  'Search items in the wizard tab and add them here to monitor their stock levels.',
                  textAlign: TextAlign.center,
                  style: GoogleFonts.inter(fontSize: 12, color: const Color(0xFF64748B), height: 1.5),
                ),
              ],
            ),
          ),
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _handleRefresh,
      color: const Color(0xFF6C63FF),
      child: ListView.builder(
        padding: const EdgeInsets.all(16.0),
        itemCount: products.length,
        itemBuilder: (context, index) {
          final product = products[index];
          final isChecking = _checkingItems[product.url] ?? false;
          final isInstock = product.status == 'IN_STOCK';
          final isOutstock = product.status == 'OUT_OF_STOCK';

          Color statusBgColor = const Color(0xFFE2E8F0);
          Color statusTxtColor = const Color(0xFF475569);
          if (isInstock) {
            statusBgColor = const Color(0xFFECFDF5);
            statusTxtColor = const Color(0xFF059669);
          } else if (isOutstock) {
            statusBgColor = const Color(0xFFFFF1F2);
            statusTxtColor = const Color(0xFFE11D48);
          }

          return Dismissible(
            key: Key(product.url),
            direction: DismissDirection.endToStart,
            background: Container(
              alignment: Alignment.centerRight,
              padding: const EdgeInsets.only(right: 24.0),
              margin: const EdgeInsets.symmetric(vertical: 6),
              decoration: BoxDecoration(
                color: const Color(0xFFE11D48),
                borderRadius: BorderRadius.circular(16),
              ),
              child: const Icon(Icons.delete_outline, color: Colors.white, size: 28),
            ),
            onDismissed: (direction) {
              _deleteProduct(api, provider, product.url, product.name);
            },
            child: Container(
              margin: const EdgeInsets.symmetric(vertical: 6),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(16),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.01),
                    blurRadius: 10,
                    offset: const Offset(0, 4),
                  )
                ],
              ),
              child: ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                title: Text(
                  product.name,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: GoogleFonts.inter(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: const Color(0xFF1E293B),
                  ),
                ),
                subtitle: Padding(
                  padding: const EdgeInsets.only(top: 8.0),
                  child: Row(
                    children: [
                      // Stock status badge
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                        decoration: BoxDecoration(
                          color: statusBgColor,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Text(
                          product.status,
                          style: GoogleFonts.inter(
                            fontSize: 10,
                            fontWeight: FontWeight.bold,
                            color: statusTxtColor,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                trailing: SizedBox(
                  width: 44,
                  height: 44,
                  child: isChecking
                      ? const Padding(
                          padding: EdgeInsets.all(12.0),
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            valueColor: AlwaysStoppedAnimation<Color>(Color(0xFF6C63FF)),
                          ),
                        )
                      : IconButton(
                          tooltip: 'Check stock now',
                          icon: const Icon(Icons.rotate_right_outlined, color: Color(0xFF6C63FF)),
                          onPressed: () => _recheckStock(api, provider, product.url),
                        ),
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}
