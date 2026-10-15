/**
 * NIQ (Natural Intelligence Query) Frontend Application
 * Clean Code Architecture - Frontend JavaScript
 */

class NIQApp {
    constructor() {
        this.apiBaseUrl = '/api';
        this.elements = this.initializeElements();
        this.state = this.initializeState();
        this.bindEvents();
        this.initializeSidebar();
        this.initializeScrollAnimations();
        this.initializeDashboard();
        this.initializeApp();
    }

    /**
     * Initialize DOM elements
     */
    initializeElements() {
        const elements = {
            // Sidebar elements
            sidebar: document.getElementById('sidebar'),
            sidebarToggle: document.getElementById('sidebar-toggle'),
            sidebarNavItems: document.querySelectorAll('.sidebar-nav-item'),
            
            // Header element
            header: document.getElementById('header'),
            
            // Progress bar elements (removed)
            // progressFill: document.getElementById('progress-fill'),
            // progressSteps: document.querySelectorAll('.progress-step'),
            
            // Database connection elements
            dbConnInput: document.getElementById('db_conn'),
            dbDriverInput: document.getElementById('db_driver'),
            dbServerInput: document.getElementById('db_server'),
            dbDatabaseInput: document.getElementById('db_database'),
            dbUsernameInput: document.getElementById('db_username'),
            dbPasswordInput: document.getElementById('db_password'),
            setDbBtn: document.getElementById('set-db-btn'),
            clearDbBtn: document.getElementById('clear-db-btn'),
            dbStatus: document.getElementById('db-status'),
            
            // Table selection elements
            tableSelect: document.getElementById('table_name'),
            
            // Schema elements
            columnsDiv: document.getElementById('columns'),
            
            // Query elements
            questionTextarea: document.getElementById('question'),
            sendQueryBtn: document.getElementById('send-query-btn'),
            clearBtn: document.getElementById('clear-btn'),
            
            // Results elements
            resultsDiv: document.getElementById('results'),
            
            // Navigation elements
            pageContents: document.querySelectorAll('.page-content'),
            
            // Saved queries elements
            refreshQueriesBtn: document.getElementById('refresh-queries-btn'),
            exportQueriesBtn: document.getElementById('export-queries-btn'),
            clearAllQueriesBtn: document.getElementById('clear-all-queries-btn'),
            savedQueriesList: document.getElementById('saved-queries-list'),
            
            // Dashboard elements
            querySelector: document.getElementById('query-selector'),
            chartTypeBtns: document.querySelectorAll('.chart-type-btn'),
            createChartBtn: document.getElementById('create-chart-btn'),
            dashboardChart: document.getElementById('dashboard-chart'),
            chartContainer: document.getElementById('chart-container'),
            chartInfo: document.getElementById('chart-info'),
            chartDetails: document.getElementById('chart-details'),
            
            // Sections for scroll reveal
            sections: document.querySelectorAll('.section'),
            
            // Header elements
            header: document.querySelector('.modern-header'),
        };

        // Debug: Check for missing elements (only log if element is actually missing)
        for (const [name, element] of Object.entries(elements)) {
            if (!element && !['sections', 'sidebarNavItems', 'pageContents', 'chartTypeBtns'].includes(name)) {
                console.warn(`Element not found: ${name} - Bu element opsiyonel olabilir`);
            }
        }

        return elements;
    }

    /**
     * Initialize application state
     */
    initializeState() {
        return {
            isConnected: false,
            selectedTables: [],
            isLoading: false,
            currentChart: null,
            selectedChartType: null,
            selectedQueryId: null
        };
    }

    /**
     * Initialize sidebar toggle functionality
     */
    initializeSidebar() {
        // Load sidebar state from localStorage
        const sidebarCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
        
        if (!sidebarCollapsed) {
            document.body.classList.remove('sidebar-collapsed');
            if (this.elements.sidebar) this.elements.sidebar.classList.remove('collapsed');
            if (this.elements.sidebarToggle) this.elements.sidebarToggle.classList.add('active');
        }
        
        // Sidebar toggle click event
        if (this.elements.sidebarToggle) {
            this.elements.sidebarToggle.addEventListener('click', () => {
                this.toggleSidebar();
            });
        }
        
        // Sidebar navigation items
        if (this.elements.sidebarNavItems) {
            this.elements.sidebarNavItems.forEach(item => {
                item.addEventListener('click', (e) => {
                    const page = e.currentTarget.dataset.page;
                    this.switchPage(page);
                });
            });
        }
    }

    /**
     * Toggle sidebar open/closed
     */
    toggleSidebar() {
        const isCollapsed = document.body.classList.toggle('sidebar-collapsed');
        if (this.elements.sidebar) this.elements.sidebar.classList.toggle('collapsed');
        if (this.elements.sidebarToggle) this.elements.sidebarToggle.classList.toggle('active');
        
        // Save state to localStorage
        localStorage.setItem('sidebarCollapsed', isCollapsed);
    }

    /**
     * Initialize scroll animations
     */
    initializeScrollAnimations() {
        // Sticky header shrink on scroll
        let lastScroll = 0;
        window.addEventListener('scroll', () => {
            const currentScroll = window.pageYOffset;
            
            if (currentScroll > 100) {
                if (this.elements.header) this.elements.header.classList.add('shrink');
            } else {
                if (this.elements.header) this.elements.header.classList.remove('shrink');
            }
            
            lastScroll = currentScroll;
        });
        
        // Intersection Observer for section reveal animations
        const observerOptions = {
            threshold: 0.1,
            rootMargin: '0px 0px -100px 0px'
        };
        
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('revealed');
                }
            });
        }, observerOptions);
        
        // Observe all sections
        this.elements.sections.forEach(section => {
            observer.observe(section);
        });
    }

    /**
     * Initialize dashboard functionality
     */
    initializeDashboard() {
        // Render dynamic quick actions
        this.renderQuickActions();
        
        // Update dashboard stats
        this.updateDashboardStats();
        
        // Load recent activities
        this.loadRecentActivities();
    }

    /**
     * Render quick actions based on connection status
     */
    renderQuickActions() {
        const actionsGrid = document.getElementById('dynamic-actions-grid');
        if (!actionsGrid) return;

        const actions = this.getOrderedActions();
        
        const actionsHtml = actions.map(action => `
            <button class="action-card ${action.primary ? 'primary' : ''}" data-action="${action.id}">
                <div class="action-icon">
                    <div class="${action.icon}"></div>
                </div>
                <div class="action-content">
                    <h3>${action.title}</h3>
                    <p>${action.description}</p>
                </div>
                <div class="action-arrow">
                    <div class="arrow-icon"></div>
                </div>
            </button>
        `).join('');

        actionsGrid.innerHTML = actionsHtml;

        // Add event listeners to new action cards
        const actionCards = actionsGrid.querySelectorAll('.action-card');
        actionCards.forEach(card => {
            card.addEventListener('click', (e) => {
                const action = e.currentTarget.dataset.action;
                this.handleQuickAction(action);
            });
        });
    }

    /**
     * Get ordered actions based on connection status
     */
    getOrderedActions() {
        const baseActions = [
            {
                id: 'connect-db',
                icon: 'icon-connect',
                title: 'Veritabanı Bağla',
                description: 'SQL Server bağlantısı kur',
                primary: !this.state.isConnected,
                priority: this.state.isConnected ? 4 : 1
            },
            {
                id: 'new-query',
                icon: 'icon-query',
                title: 'Yeni Sorgu',
                description: 'Doğal dille SQL oluştur',
                primary: this.state.isConnected,
                priority: this.state.isConnected ? 1 : 2
            },
            {
                id: 'view-history',
                icon: 'icon-history',
                title: 'Sorgu Geçmişi',
                description: 'Önceki sorgularını görüntüle',
                primary: false,
                priority: 3
            },
            {
                id: 'view-charts',
                icon: 'icon-charts',
                title: 'Grafikler',
                description: 'Veri görselleştirmeleri',
                primary: false,
                priority: 4
            }
        ];

        // Sort by priority
        return baseActions.sort((a, b) => a.priority - b.priority);
    }


    /**
     * Handle quick action clicks
     */
    handleQuickAction(action) {
        switch(action) {
            case 'new-query':
                this.switchPage('query-page');
                // Focus on question textarea if exists
                setTimeout(() => {
                    const questionInput = document.getElementById('question');
                    if (questionInput) questionInput.focus();
                }, 300);
                break;
            case 'connect-db':
                this.switchPage('query-page');
                // Scroll to database section
                setTimeout(() => {
                    this.scrollToSection('db-section');
                }, 300);
                break;
            case 'view-history':
                this.switchPage('history-page');
                break;
            case 'view-charts':
                this.switchPage('charts-page');
                break;
        }
    }

    /**
     * Update dashboard statistics
     */
    updateDashboardStats() {
        // Update connection status in header
        this.updateConnectionStatus();
        
        // Update stats from localStorage or API
        this.updateStatFromStorage();
        
        // Re-render quick actions with new connection status
        this.renderQuickActions();
    }

    /**
     * Update connection status in header
     */
    updateConnectionStatus() {
        const connectionBadge = document.getElementById('connection-badge');
        const badgeText = connectionBadge?.querySelector('.badge-text');
        
        if (this.state.isConnected) {
            connectionBadge?.classList.add('connected');
            if (badgeText) badgeText.textContent = `Bağlı: ${this.state.currentDatabase || 'Veritabanı'}`;
        } else {
            connectionBadge?.classList.remove('connected');
            if (badgeText) badgeText.textContent = 'Bağlantı Yok';
        }
    }

    /**
     * Update stats from localStorage
     */
    updateStatFromStorage() {
        try {
            const queryHistory = JSON.parse(localStorage.getItem('niq_query_history') || '[]');
            const totalQueries = queryHistory.length;
            const successfulQueries = queryHistory.filter(q => q.success).length;
            const successRate = totalQueries > 0 ? Math.round((successfulQueries / totalQueries) * 100) : 0;
            const lastQuery = queryHistory.length > 0 ? queryHistory[0].question?.substring(0, 30) + '...' : '-';

            // Update DOM elements
            const totalQueriesEl = document.getElementById('total-queries');
            const connectedDbEl = document.getElementById('connected-db');
            const lastQueryEl = document.getElementById('last-query');
            const successRateEl = document.getElementById('success-rate');

            if (totalQueriesEl) totalQueriesEl.textContent = totalQueries;
            if (connectedDbEl) connectedDbEl.textContent = this.state.currentDatabase || '-';
            if (lastQueryEl) lastQueryEl.textContent = lastQuery;
            if (successRateEl) successRateEl.textContent = totalQueries > 0 ? `%${successRate}` : '-';
        } catch (error) {
            console.warn('Error updating stats from storage:', error);
        }
    }

    /**
     * Load recent activities
     */
    loadRecentActivities() {
        try {
            const queryHistory = JSON.parse(localStorage.getItem('niq_query_history') || '[]');
            const recentActivities = queryHistory.slice(0, 5); // Son 5 aktivite

            const activitiesContainer = document.getElementById('recent-activities');
            if (!activitiesContainer) return;

            if (recentActivities.length === 0) {
                activitiesContainer.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-icon">
                            <div class="icon-activity"></div>
                        </div>
                        <h3>Henüz aktivite yok</h3>
                        <p>İlk sorgunuzu oluşturun ve aktivitelerinizi burada görün</p>
                    </div>
                `;
                return;
            }

            const activitiesHtml = recentActivities.map(activity => `
                <div class="activity-item">
                    <div class="activity-icon">
                        <div class="${activity.success ? 'icon-success' : 'icon-error'}"></div>
                    </div>
                    <div class="activity-content">
                        <div class="activity-question">${activity.question}</div>
                        <div class="activity-time">${this.formatTime(activity.timestamp)}</div>
                    </div>
                </div>
            `).join('');

            activitiesContainer.innerHTML = activitiesHtml;
        } catch (error) {
            console.warn('Error loading recent activities:', error);
        }
    }

    /**
     * Save query to localStorage history
     */
    saveQueryToHistory(question, success, sql) {
        try {
            const queryHistory = JSON.parse(localStorage.getItem('niq_query_history') || '[]');
            const newQuery = {
                question: question,
                success: success,
                sql: sql,
                timestamp: Date.now(),
                database: this.state.currentDatabase,
                tables: this.state.selectedTables
            };
            
            queryHistory.unshift(newQuery);
            // Keep only last 100 queries
            const trimmedHistory = queryHistory.slice(0, 100);
            localStorage.setItem('niq_query_history', JSON.stringify(trimmedHistory));
        } catch (error) {
            console.warn('Error saving query to history:', error);
        }
    }

    /**
     * Format timestamp for display
     */
    formatTime(timestamp) {
        const date = new Date(timestamp);
        const now = new Date();
        const diff = now - date;
        
        if (diff < 60000) return 'Az önce';
        if (diff < 3600000) return `${Math.floor(diff / 60000)} dakika önce`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)} saat önce`;
        return date.toLocaleDateString('tr-TR');
    }

    /**
     * Smooth scroll to section
     */
    scrollToSection(sectionId) {
        const targetSection = document.getElementById(sectionId);
        if (targetSection) {
            const headerHeight = document.querySelector('.minimal-header')?.offsetHeight || 0;
            const targetPosition = targetSection.getBoundingClientRect().top + window.pageYOffset - headerHeight - 20;
                
                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });
        }
    }

    /**
     * Bind event listeners
     */
    bindEvents() {
        // Core elements
        if (this.elements.setDbBtn) {
            this.elements.setDbBtn.addEventListener('click', () => this.setDatabaseConnection());
        }
        if (this.elements.tableSelect) {
            this.elements.tableSelect.addEventListener('change', () => this.loadColumns());
        }
        if (this.elements.clearDbBtn) {
            this.elements.clearDbBtn.addEventListener('click', () => this.clearDatabaseCredentials());
        }
        if (this.elements.sendQueryBtn) {
            this.elements.sendQueryBtn.addEventListener('click', () => this.sendQuery());
        }
        if (this.elements.clearBtn) {
            this.elements.clearBtn.addEventListener('click', () => this.clearResults());
        }
        
        // Saved queries (opsiyonel elementler)
        if (this.elements.refreshQueriesBtn) {
            this.elements.refreshQueriesBtn.addEventListener('click', () => this.loadSavedQueries());
        }
        if (this.elements.exportQueriesBtn) {
            this.elements.exportQueriesBtn.addEventListener('click', () => this.exportQueriesToFile());
        }
        if (this.elements.clearAllQueriesBtn) {
            this.elements.clearAllQueriesBtn.addEventListener('click', () => this.clearAllQueries());
        }
        
        // Enable/disable query button based on state
        if (this.elements.questionTextarea) {
            this.elements.questionTextarea.addEventListener('input', () => this.updateQueryButtonState());
        }
        if (this.elements.tableSelect) {
            this.elements.tableSelect.addEventListener('change', () => this.updateQueryButtonState());
        }
        
        // Dashboard events
        if (this.elements.querySelector) {
            this.elements.querySelector.addEventListener('change', async (e) => {
                this.state.selectedQueryId = e.target.value;
                // Reset chart type selection; user must pick
                this.state.selectedChartType = null;
                this.updateCreateChartButtonState();
                // Compute and show recommended chart without creating it
                if (this.state.selectedQueryId) {
                    await this.showRecommendedChartForSelectedQuery();
                } else {
                    this.highlightRecommendedChart(null);
                }
            });
        }
        
        if (this.elements.chartTypeBtns) {
            this.elements.chartTypeBtns.forEach(btn => {
                btn.addEventListener('click', (e) => {
                    this.selectChartType(e.target.dataset.chartType);
                });
            });
        }
        
        if (this.elements.createChartBtn) {
            this.elements.createChartBtn.addEventListener('click', () => this.createChart());
        }

        // Reports events
    }

    async clearDatabaseCredentials() {
        this.setLoading(true);
        this.showStatus('Bağlantı bilgileri temizleniyor...', 'info');
        try {
            const response = await this.apiCall('/clear_db', 'POST', {});
            if (response.success) {
                // Reset state and UI
                this.state.isConnected = false;
                if (this.elements.dbPasswordInput) this.elements.dbPasswordInput.value = '';
                if (this.elements.dbConnInput) this.elements.dbConnInput.value = '';
                if (this.elements.dbServerInput) this.elements.dbServerInput.value = '';
                if (this.elements.dbDatabaseInput) this.elements.dbDatabaseInput.value = '';
                if (this.elements.dbUsernameInput) this.elements.dbUsernameInput.value = '';
                // Disable dependent UI
                if (this.elements.tableSelect) {
                    this.elements.tableSelect.innerHTML = '<option value="">Önce veritabanı bağlantısını kurun</option>';
                    this.elements.tableSelect.disabled = true;
                }
                // Clear results and pages that depend on connection
                if (this.elements.resultsDiv) {
                    this.elements.resultsDiv.innerHTML = '<p class="placeholder">Sorgu sonuçları burada görünecek</p>';
                }
                if (this.elements.savedQueriesList) {
                    this.elements.savedQueriesList.innerHTML = `
                        <div class="no-saved-queries">
                            <div class="no-saved-queries-icon">🔌</div>
                            <p>Önce veritabanı bağlantısını kurun</p>
                        </div>`;
                }
                if (this.elements.querySelector) {
                    this.elements.querySelector.innerHTML = '<option value="">Önce sorgu seçin...</option>';
                }
                if (this.elements.createChartBtn) {
                    this.elements.createChartBtn.disabled = true;
                }
                if (this.elements.scheduledQueriesList) {
                    this.elements.scheduledQueriesList.innerHTML = `
                        <p class="placeholder">Önce veritabanı bağlantısını kurun</p>`;
                }
                if (this.elements.dashboardChart) {
                    this.elements.dashboardChart.style.display = 'none';
                }
                this.showStatus('Bağlantı bilgileri temizlendi', 'success');
            } else {
                this.showStatus(`Temizleme hatası: ${response.error}`, 'error');
            }
        } catch (error) {
            this.showStatus(`Temizleme hatası: ${error.message}`, 'error');
        } finally {
            this.setLoading(false);
        }
    }

    /**
     * Initialize application
     */
    async initializeApp() {
        this.showStatus('Uygulama başlatılıyor...', 'info');
        this.showStatus('Veritabanı bağlantısı kurulmasını bekliyor...', 'info');

        try {
            const health = await this.apiCall('/health', 'GET');
            this.state.aiAvailable = health.data.ai_service_available;
            if (health.data.database_connected) {
                // Sunucu tarafında oturum sürüyor (yeniden yükleme / uygulama yeniden açılışı)
                this.state.isConnected = true;
                this.elements.tableSelect.disabled = false;
                const schemaSection = document.getElementById('schema-section');
                if (schemaSection) schemaSection.style.display = 'block';
                this.updateConnectionStatus();
                await this.loadTables();
            }
            if (!health.data.ai_service_available) {
                this.showStatus('AI için API anahtarı gerekli: Ayarlar sayfasından ekleyin.', 'error');
            }
        } catch (error) {
            console.warn('Sağlık kontrolü başarısız:', error);
        }
        
        // Load saved queries if database is connected and element exists
        if (this.state.isConnected && this.elements.savedQueriesList) {
            await this.loadSavedQueries();
        }

        // If reports tables select exists and we are connected later, it will be enabled in setDatabaseConnection
    }

    /**
     * Set database connection
     */
    async setDatabaseConnection() {
        const payload = window.NIQConnections ? window.NIQConnections.getPayload() : null;
        const problem = window.NIQConnections ? window.NIQConnections.validate(payload) : 'Bağlantı modülü yüklenemedi';
        if (problem) {
            this.showStatus(problem, 'error');
            return;
        }
        const database = payload.database;

        this.setLoading(true);
        this.showStatus('Veritabanı bağlantısı kuruluyor...', 'info');

        try {
            const response = await this.apiCall('/set_db', 'POST', payload);

            if (response.success) {
                await this.onConnected(database);
            } else {
                this.showStatus(`Bağlantı hatası: ${response.error}`, 'error');
            }
        } catch (error) {
            this.showStatus(`Bağlantı hatası: ${error.message}`, 'error');
        } finally {
            this.setLoading(false);
        }
    }

    /**
     * Bağlantı kurulduktan sonra arayüzü hazırlar (yeni bağlantı veya kayıtlı profil)
     */
    async onConnected(databaseName) {
        this.state.isConnected = true;
        this.state.currentDatabase = String(databaseName || '').split(/[\\/]/).pop();
        this.showStatus('Veritabanı bağlantısı başarılı!', 'success');
        if (this.elements.dbPasswordInput) this.elements.dbPasswordInput.value = '';
        this.elements.tableSelect.disabled = false;
        const schemaSection = document.getElementById('schema-section');
        if (schemaSection) schemaSection.style.display = 'block';
        this.updateConnectionStatus();
        this.updateDashboardStats();
        await this.loadTables();
        if (this.elements.savedQueriesList) {
            await this.loadSavedQueries();
        }
    }

    /**
     * Load tables from database
     */
    async loadTables() {
        if (!this.state.isConnected) {
            this.showStatus('Önce veritabanı bağlantısını kurun', 'error');
            return;
        }

        try {
            const response = await this.apiCall('/tables', 'GET');
            
            if (response.success) {
                this.populateTableSelect(response.data);
                this.showStatus(`${response.data.length} tablo yüklendi`, 'success');
            } else {
                this.showStatus(`Tablo yükleme hatası: ${response.error}`, 'error');
            }
        } catch (error) {
            this.showStatus(`Tablo yükleme hatası: ${error.message}`, 'error');
        }
    }


    /**
     * Populate table select element
     */
    populateTableSelect(tables) {
        this.elements.tableSelect.innerHTML = '';
        
        if (tables.length === 0) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'Veritabanında tablo bulunamadı';
            this.elements.tableSelect.appendChild(option);
            return;
        }

        tables.forEach(table => {
            const option = document.createElement('option');
            option.value = table;
            option.textContent = table;
            this.elements.tableSelect.appendChild(option);
        });
    }

    /**
     * Load columns for selected tables
     */
    async loadColumns() {
        const selectedOptions = Array.from(this.elements.tableSelect.selectedOptions);
        const selectedTables = selectedOptions.map(option => option.value);
        
        this.state.selectedTables = selectedTables;

        if (selectedTables.length === 0) {
            this.elements.columnsDiv.innerHTML = '<p class="placeholder">Tablolar seçildiğinde kolonlar burada görünecek</p>';
            return;
        }

        try {
            const response = await this.apiCall('/columns', 'POST', {
                tables: selectedTables
            });

            if (response.success) {
                this.displayColumns(response.data);
            } else {
                this.showStatus(`Kolon yükleme hatası: ${response.error}`, 'error');
            }
        } catch (error) {
            this.showStatus(`Kolon yükleme hatası: ${error.message}`, 'error');
        }
    }

    /**
     * Display columns information
     */
    displayColumns(columnsData) {
        let html = '';
        
        for (const [tableName, columns] of Object.entries(columnsData)) {
            html += `
                <div class="table-schema">
                    <h4>${tableName}</h4>
                    <div class="columns-list">
                        ${columns.map(col => `<span class="column-tag">${col}</span>`).join('')}
                    </div>
                </div>
            `;
        }
        
        this.elements.columnsDiv.innerHTML = html;
    }

    /**
     * Send query to backend
     */
    async sendQuery() {
        const question = this.elements.questionTextarea.value.trim();
        const selectedTables = this.state.selectedTables;

        if (!question) {
            this.showStatus('Lütfen bir soru girin', 'error');
            return;
        }

        if (selectedTables.length === 0) {
            this.showStatus('Lütfen en az bir tablo seçin', 'error');
            return;
        }

        // Scroll to results section
        this.scrollToSection('results-section');
        
        this.setLoading(true);
        this.showStatus('Sorgu işleniyor...', 'info');

        try {
            const response = await this.apiCall('/query', 'POST', {
                question: question,
                tables: selectedTables
            });

            this.displayResults(response);
            
            // Save to localStorage for dashboard stats
            this.saveQueryToHistory(question, response.success, response.sql);
            
            if (response.success) {
                this.showStatus('Sorgu başarıyla çalıştırıldı', 'success');
                // Update dashboard stats
                this.updateDashboardStats();
            } else {
                this.showStatus(`Sorgu hatası: ${response.error}`, 'error');
            }
        } catch (error) {
            if (error.payload) {
                this.displayResults(error.payload);
                this.saveQueryToHistory(question, false, error.payload.sql);
            }
            this.showStatus(`Sorgu hatası: ${error.message}`, 'error');
        } finally {
            this.setLoading(false);
        }
    }

    /**
     * Display query results
     */
    displayResults(response) {
        if (response.success && response.results) {
            this.displayTableResults(response.results, response.sql, response);
        } else if (response.success) {
            this.displayMessageResult(response.message || 'Tamamlandı', response.sql);
        } else {
            this.displayErrorResult(response.error, response.sql, response);
        }
    }

    /**
     * Render the SQL block (escaped) with the model's explanation
     */
    renderSqlBlock(sql, meta = {}) {
        const explanation = meta.explanation ? `<p class="ai-explanation">${this.escapeHtml(meta.explanation)}</p>` : '';
        const retried = meta.attempts > 1 ? `<p class="ai-note">AI sorguyu ${meta.attempts} denemede düzeltti.</p>` : '';
        return `
            <div class="sql-query">
                <strong>SQL:</strong>
                <pre>${this.escapeHtml(sql || 'SQL oluşturulamadı')}</pre>
            </div>
            ${explanation}${retried}`;
    }

    /**
     * Display table results
     */
    displayTableResults(results, sql, meta = {}) {
        const analysis = meta.analysis && (meta.analysis.summary || meta.analysis.analysis)
            ? `<div class="ai-analysis"><strong>AI yorumu</strong><p>${this.escapeHtml(meta.analysis.summary)}</p><p>${this.escapeHtml(meta.analysis.analysis)}</p></div>`
            : '';
        const truncated = meta.truncated
            ? '<p class="ai-note">Sonuç satır limitine takıldı; tüm satırlar gösterilmiyor (Ayarlar > Satır limiti).</p>'
            : '';

        if (results.length === 0) {
            this.elements.resultsDiv.innerHTML = `
                <div class="result-section">
                    <h3>Sorgu Sonuçları</h3>
                    ${this.renderSqlBlock(sql, meta)}
                    <div class="no-results"><p>Sorgu sonucu bulunamadı</p></div>
                </div>`;
            return;
        }

        const headers = Object.keys(results[0]);
        const tableRows = results.map(row =>
            `<tr>${headers.map(header => `<td>${this.escapeHtml(row[header])}</td>`).join('')}</tr>`
        ).join('');

        this.elements.resultsDiv.innerHTML = `
            <div class="result-section">
                <h3>Sorgu Sonuçları (${results.length} satır)</h3>
                ${this.renderSqlBlock(sql, meta)}
                ${analysis}${truncated}
                <div class="table-container">
                    <table class="results-table">
                        <thead><tr>${headers.map(header => `<th>${this.escapeHtml(header)}</th>`).join('')}</tr></thead>
                        <tbody>${tableRows}</tbody>
                    </table>
                </div>
            </div>`;
    }

    /**
     * Display message result
     */
    displayMessageResult(message, sql) {
        this.elements.resultsDiv.innerHTML = `
            <div class="result-section">
                <h3>Sorgu Tamamlandı</h3>
                ${this.renderSqlBlock(sql)}
                <div class="message-result"><p>${this.escapeHtml(message)}</p></div>
            </div>`;
    }

    /**
     * Display error result
     */
    displayErrorResult(error, sql, meta = {}) {
        this.elements.resultsDiv.innerHTML = `
            <div class="result-section">
                <h3>Sorgu Hatası</h3>
                ${this.renderSqlBlock(sql, meta)}
                <div class="error-result"><p><strong>Hata:</strong> ${this.escapeHtml(error)}</p></div>
            </div>`;
    }

    /**
     * Clear results and form
     */
    clearResults() {
        this.elements.resultsDiv.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">
                    <div class="icon-chart"></div>
                </div>
                <h3>Sorgu sonuçlarınız burada görünecek</h3>
                <p>Yukarıdan bir sorgu çalıştırın ve sonuçları görün</p>
            </div>
        `;
        this.elements.questionTextarea.value = '';
        this.showStatus('Sonuçlar temizlendi', 'info');
    }

    /**
     * Update query button state based on form validity
     */
    updateQueryButtonState() {
        const hasQuestion = this.elements.questionTextarea.value.trim().length > 0;
        const hasTables = this.state.selectedTables.length > 0;
        const isConnected = this.state.isConnected;
        
        this.elements.sendQueryBtn.disabled = !(hasQuestion && hasTables && isConnected);
    }

    /**
     * Set loading state
     */
    setLoading(isLoading) {
        this.state.isLoading = isLoading;
        this.elements.setDbBtn.disabled = isLoading;
        this.elements.sendQueryBtn.disabled = isLoading || !this.canSendQuery();
    }

    /**
     * Check if query can be sent
     */
    canSendQuery() {
        const hasQuestion = this.elements.questionTextarea.value.trim().length > 0;
        const hasTables = this.state.selectedTables.length > 0;
        const isConnected = this.state.isConnected;
        
        return hasQuestion && hasTables && isConnected;
    }

    /**
     * Show status message
     */
    showStatus(message, type = 'info') {
        this.elements.dbStatus.textContent = message;
        this.elements.dbStatus.className = `status-message status-${type}`;
        
        // Auto-hide success messages after 3 seconds
        if (type === 'success') {
            setTimeout(() => {
                this.elements.dbStatus.textContent = '';
                this.elements.dbStatus.className = 'status-message';
            }, 3000);
        }
    }

    /**
     * Make API call
     */
    async apiCall(endpoint, method = 'GET', data = null) {
        const url = `${this.apiBaseUrl}${endpoint}`;
        const options = {
            method: method,
            headers: {
                'Content-Type': 'application/json'
            }
        };

        if (data && method !== 'GET') {
            options.body = JSON.stringify(data);
        }

        const response = await fetch(url, options);
        const result = await response.json();

        if (!response.ok) {
            const error = new Error(result.error || 'API call failed');
            error.payload = result;
            throw error;
        }

        return result;
    }

    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Format SQL query for better readability
     */
    formatSQL(sql) {
        if (!sql) return '';
        
        // Basic SQL formatting
        return sql
            .replace(/\bSELECT\b/gi, '\nSELECT')
            .replace(/\bFROM\b/gi, '\nFROM')
            .replace(/\bWHERE\b/gi, '\nWHERE')
            .replace(/\bJOIN\b/gi, '\nJOIN')
            .replace(/\bLEFT JOIN\b/gi, '\nLEFT JOIN')
            .replace(/\bRIGHT JOIN\b/gi, '\nRIGHT JOIN')
            .replace(/\bINNER JOIN\b/gi, '\nINNER JOIN')
            .replace(/\bGROUP BY\b/gi, '\nGROUP BY')
            .replace(/\bORDER BY\b/gi, '\nORDER BY')
            .replace(/\bHAVING\b/gi, '\nHAVING')
            .replace(/\bUNION\b/gi, '\nUNION')
            .replace(/\bINSERT INTO\b/gi, '\nINSERT INTO')
            .replace(/\bUPDATE\b/gi, '\nUPDATE')
            .replace(/\bDELETE FROM\b/gi, '\nDELETE FROM')
            .trim();
    }

    /**
     * Re-run a saved query
     */
    async reRunQuery(queryId) {
        try {
            const response = await this.apiCall(`/queries/${queryId}`, 'GET');
            
            if (response.success) {
                const query = response.data;
                
                // Switch to query page
                this.switchPage('query-page');
                
                // Fill the form with the saved query data
                this.elements.questionTextarea.value = query.question;
                
                // Select the tables that were used
                if (this.elements.tableSelect && query.tables_used) {
                    Array.from(this.elements.tableSelect.options).forEach(option => {
                        option.selected = query.tables_used.includes(option.value);
                    });
                    
                    // Trigger change event to load columns
                    this.elements.tableSelect.dispatchEvent(new Event('change'));
                }
                
                this.showStatus('Sorgu bilgileri forma yüklendi. Göndermek için "Sorguyu Gönder" butonuna tıklayın.', 'info');
            } else {
                this.showStatus(`Sorgu alınırken hata: ${response.error}`, 'error');
            }
        } catch (error) {
            this.showStatus(`Sorgu alınırken hata: ${error.message}`, 'error');
        }
    }

    /**
     * Switch between pages
     */
    switchPage(pageName) {
        // Update sidebar navigation items
        if (this.elements.sidebarNavItems) {
            this.elements.sidebarNavItems.forEach(item => {
                item.classList.toggle('active', item.dataset.page === pageName);
            });
        }

        // Update page content
        this.elements.pageContents.forEach(content => {
            content.classList.toggle('active', content.id === pageName);
        });


        // Load saved queries when switching to history page
        if (pageName === 'history-page' && this.state.isConnected && this.elements.savedQueriesList) {
            this.loadSavedQueries();
        }
        
        // Load dashboard data when switching to dashboard page
        if (pageName === 'dashboard-page' && this.state.isConnected) {
            this.loadDashboard();
        }
        
        // Load charts data when switching to charts page
        if (pageName === 'charts-page' && this.state.isConnected) {
            this.loadDashboard(); // Charts page uses same data loading
        }
    }

    /**
     * Load saved queries from the database
     */
    async loadSavedQueries() {
        if (!this.elements.savedQueriesList) {
            console.warn('savedQueriesList element not found - Saved queries tab may not be available');
            return;
        }

        if (!this.state.isConnected) {
            this.elements.savedQueriesList.innerHTML = `
                <div class="no-saved-queries">
                    <div class="no-saved-queries-icon">🔌</div>
                    <p>Önce veritabanı bağlantısını kurun</p>
                </div>
            `;
            return;
        }

        try {
            this.elements.savedQueriesList.innerHTML = '<p class="placeholder">Kayıtlı sorgular yükleniyor...</p>';
            
            const response = await this.apiCall('/queries', 'GET');
            
            if (response.success) {
                this.displaySavedQueries(response.data);
            } else {
                this.elements.savedQueriesList.innerHTML = `
                    <div class="no-saved-queries">
                        <div class="no-saved-queries-icon">
                            <div class="icon-error"></div>
                        </div>
                        <p>Sorgular yüklenirken hata oluştu: ${response.error}</p>
                    </div>
                `;
            }
        } catch (error) {
            this.elements.savedQueriesList.innerHTML = `
                <div class="no-saved-queries">
                    <div class="no-saved-queries-icon">
                        <div class="icon-error"></div>
                    </div>
                    <p>Sorgular yüklenirken hata oluştu: ${error.message}</p>
                </div>
            `;
        }
    }

    /**
     * Display saved queries
     */
    displaySavedQueries(queries) {
        if (!this.elements.savedQueriesList) {
            console.warn('savedQueriesList element not found - Saved queries tab may not be available');
            return;
        }

        if (queries.length === 0) {
            this.elements.savedQueriesList.innerHTML = `
                <div class="no-saved-queries">
                    <div class="no-saved-queries-icon">
                        <div class="icon-activity"></div>
                    </div>
                    <p>Henüz kayıtlı sorgu bulunmuyor</p>
                    <p>İlk sorgunuzu oluşturun ve otomatik olarak kaydedilecek</p>
                </div>
            `;
            return;
        }

        const queriesHtml = queries.map(query => this.createSavedQueryHTML(query)).join('');
        this.elements.savedQueriesList.innerHTML = queriesHtml;
    }

    /**
     * Create HTML for a saved query
     */
    createSavedQueryHTML(query) {
        const createdDate = new Date(query.created_at).toLocaleString('tr-TR');
        const statusText = query.is_successful ? 'Başarılı' : 'Hatalı';
        const statusClass = query.is_successful ? 'success' : 'error';
        
        const tablesHtml = query.tables_used.map(table => 
            `<span class="saved-query-table-tag">${this.escapeHtml(table)}</span>`
        ).join('');

        // Format the SQL query for better readability
        const formattedSql = this.formatSQL(query.sql_query);

        // Generate results HTML
        let resultsHtml = '';
        if (query.is_successful && query.query_results && query.query_results.length > 0) {
            // SELECT query with results
            const headers = Object.keys(query.query_results[0]);
            const tableRows = query.query_results.slice.map(row => 
                `<tr>${headers.map(header => `<td>${this.escapeHtml(String(row[header] || ''))}</td>`).join('')}</tr>`
            ).join('');
            
            const moreRowsText = query.query_results.length > 10 
                ? `<p class="more-results-info">... ve ${query.query_results.length - 10} satır daha (Toplam: ${query.query_results.length} satır)</p>` 
                : '';

            resultsHtml = `
                <div class="saved-query-results">
                    <h4>Sorgu Sonuçları (${query.query_results.length} satır):</h4>
                    <div class="table-container">
                        <table class="results-table">
                            <thead>
                                <tr>${headers.map(header => `<th>${this.escapeHtml(header)}</th>`).join('')}</tr>
                            </thead>
                            <tbody>
                                ${tableRows}
                            </tbody>
                        </table>
                    </div>
                    ${moreRowsText}
                </div>
            `;
        } else if (query.is_successful && query.result_message) {
            // Non-SELECT query with message
            resultsHtml = `
                <div class="saved-query-success">
                    <h4>Sorgu Başarıyla Çalıştırıldı</h4>
                    <p>${this.escapeHtml(query.result_message)}</p>
                </div>
            `;
        } else if (query.is_successful && query.query_results && query.query_results.length === 0) {
            // SELECT query with no results
            resultsHtml = `
                <div class="saved-query-no-results">
                    <h4>Sorgu Sonuçları:</h4>
                    <p>Sorgu başarılı ancak sonuç bulunamadı.</p>
                </div>
            `;
        } else if (!query.is_successful && query.error_message) {
            // Error
            resultsHtml = `
                <div class="saved-query-error">
                    <h4>Hata Mesajı:</h4>
                    <p>${this.escapeHtml(query.error_message)}</p>
                </div>
            `;
        }

        return `
            <div class="saved-query-item">
                <div class="saved-query-header">
                    <div class="saved-query-info">
                        <div class="saved-query-id">Sorgu #${query.id}</div>
                        <div class="saved-query-question">
                            <strong>Soru:</strong> ${this.escapeHtml(query.question)}
                        </div>
                        <div class="saved-query-meta">
                            <span>${createdDate}</span>
                            <span class="saved-query-status ${statusClass}">${statusText}</span>
                        </div>
                        <div class="saved-query-tables">
                            <strong>Kullanılan Tablolar:</strong> ${tablesHtml}
                        </div>
                    </div>
                </div>
                
                <div class="saved-query-sql-section">
                    <h4>SQL Sorgusu:</h4>
                    <div class="sql-code-block">
                        <pre><code>${formattedSql}</code></pre>
                    </div>
                </div>
                
                ${resultsHtml}
                
                <div class="saved-query-actions">
                    <button class="btn btn-sm btn-secondary" onclick="app.copyQuery(${query.id})">
                        SQL'i Kopyala
                    </button>
                    <button class="btn btn-sm btn-primary" onclick="app.reRunQuery(${query.id})">
                        Tekrar Çalıştır
                    </button>
                    <button class="btn btn-sm btn-danger" onclick="app.deleteQuery(${query.id})">
                        Sil
                    </button>
                </div>
            </div>
        `;
    }

    /**
     * Delete a saved query
     */
    async deleteQuery(queryId) {
        if (!confirm('Bu sorguyu silmek istediğinizden emin misiniz?')) {
            return;
        }

        try {
            const response = await this.apiCall(`/queries/${queryId}`, 'DELETE');
            
            if (response.success) {
                this.showStatus('Sorgu başarıyla silindi', 'success');
                await this.loadSavedQueries(); // Refresh the list
            } else {
                this.showStatus(`Sorgu silinirken hata: ${response.error}`, 'error');
            }
        } catch (error) {
            this.showStatus(`Sorgu silinirken hata: ${error.message}`, 'error');
        }
    }

    /**
     * Copy query to clipboard
     */
    async copyQuery(queryId) {
        try {
            const response = await this.apiCall(`/queries/${queryId}`, 'GET');
            
            if (response.success) {
                await navigator.clipboard.writeText(response.data.sql_query);
                this.showStatus('SQL sorgusu panoya kopyalandı', 'success');
            } else {
                this.showStatus(`Sorgu alınırken hata: ${response.error}`, 'error');
            }
        } catch (error) {
            this.showStatus(`Kopyalama hatası: ${error.message}`, 'error');
        }
    }

    /**
     * Export queries to file
     */
    async exportQueriesToFile() {
        if (!this.state.isConnected) {
            this.showStatus('Önce veritabanı bağlantısını kurun', 'error');
            return;
        }

        try {
            const response = await this.apiCall('/queries', 'GET');
            
            if (response.success && response.data.length > 0) {
                // Create file content
                let fileContent = `# NIQ - Kayıtlı Sorgular\n`;
                fileContent += `# Oluşturulma Tarihi: ${new Date().toLocaleString('tr-TR')}\n`;
                fileContent += `# Toplam Sorgu Sayısı: ${response.data.length}\n\n`;
                
                response.data.forEach((query, index) => {
                    const createdDate = new Date(query.created_at).toLocaleString('tr-TR');
                    const status = query.is_successful ? 'BAŞARILI' : 'HATALI';
                    
                    fileContent += `## Sorgu #${query.id} - ${status}\n`;
                    fileContent += `**Tarih:** ${createdDate}\n`;
                    fileContent += `**Soru:** ${query.question}\n`;
                    fileContent += `**Tablolar:** ${query.tables_used.join(', ')}\n`;
                    
                    if (query.error_message) {
                        fileContent += `**Hata:** ${query.error_message}\n`;
                    }
                    
                    fileContent += `\n**SQL Sorgusu:**\n\`\`\`sql\n${query.sql_query}\n\`\`\`\n\n`;
                    fileContent += `---\n\n`;
                });
                
                // Create and download file
                const blob = new Blob([fileContent], { type: 'text/markdown' });
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `sorgular_${new Date().toISOString().split('T')[0]}.md`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                
                this.showStatus('Sorgular başarıyla dosyaya kaydedildi', 'success');
            } else {
                this.showStatus('Kaydedilecek sorgu bulunamadı', 'info');
            }
        } catch (error) {
            this.showStatus(`Dosya kaydetme hatası: ${error.message}`, 'error');
        }
    }

    /**
     * Clear all saved queries
     */
    async clearAllQueries() {
        if (!confirm('Tüm kayıtlı sorguları silmek istediğinizden emin misiniz? Bu işlem geri alınamaz!')) {
            return;
        }

        try {
            // Get all queries first
            const response = await this.apiCall('/queries', 'GET');
            
            if (response.success && response.data.length > 0) {
                // Delete each query
                for (const query of response.data) {
                    await this.apiCall(`/queries/${query.id}`, 'DELETE');
                }
                
                this.showStatus('Tüm sorgular başarıyla silindi', 'success');
                await this.loadSavedQueries(); // Refresh the list
            } else {
                this.showStatus('Silinecek sorgu bulunamadı', 'info');
            }
        } catch (error) {
            this.showStatus(`Sorgular silinirken hata: ${error.message}`, 'error');
        }
    }

    /* ==========================================================================
       DASHBOARD METHODS
       ========================================================================== */

    /**
     * Load dashboard data
     */
    async loadDashboard() {
        if (!this.state.isConnected) {
            return;
        }

        try {
            const response = await this.apiCall('/queries', 'GET');
            
            if (response.success) {
                this.populateQuerySelector(response.data);
            }
        } catch (error) {
            console.error('Error loading dashboard:', error);
        }
    }

    /**
     * Populate query selector dropdown
     */
    populateQuerySelector(queries) {
        if (!this.elements.querySelector) return;
        
        this.elements.querySelector.innerHTML = '<option value="">Sorgu seçin...</option>';
        
        // Only show queries with results
        const queriesWithResults = queries.filter(q => q.query_results && q.query_results.length > 0);
        
        if (queriesWithResults.length === 0) {
            this.elements.querySelector.innerHTML = '<option value="">Grafik oluşturulabilecek sorgu yok</option>';
            return;
        }
        
        queriesWithResults.forEach(query => {
            const option = document.createElement('option');
            option.value = query.id;
            option.textContent = `#${query.id} - ${query.question.substring(0, 60)}${query.question.length > 60 ? '...' : ''}`;
            this.elements.querySelector.appendChild(option);
        });
    }

    /**
     * Select chart type
     */
    selectChartType(chartType) {
        this.state.selectedChartType = chartType;
        
        // Update button states
        this.elements.chartTypeBtns.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.chartType === chartType);
        });
        this.updateCreateChartButtonState();
    }

    /**
     * Update create chart button state
     */
    updateCreateChartButtonState() {
        if (this.elements.createChartBtn) {
            const enabled = !!(this.state.selectedQueryId && this.state.selectedChartType);
            this.elements.createChartBtn.disabled = !enabled;
        }
    }

    /**
     * Create chart from selected query
     */
    async createChart() {
        if (!this.state.selectedQueryId) return;
        if (!this.state.selectedChartType) {
            this.showStatus('Önce bir grafik tipi seçin', 'error');
            return;
        }

        try {
            const response = await this.apiCall(`/queries/${this.state.selectedQueryId}`, 'GET');
            
            if (response.success && response.data.query_results) {
                const query = response.data;
                this.renderChart(query);
            } else {
                this.showStatus('Sorgu verileri alınamadı', 'error');
            }
        } catch (error) {
            this.showStatus(`Grafik oluşturma hatası: ${error.message}`, 'error');
        }
    }

    /**
     * Render chart with query data
     */
    renderChart(query) {
        const results = query.query_results;
        
        if (!results || results.length === 0) {
            this.displayChartError('Sorgu sonucu boş');
            return;
        }

        // Analyze data
        const chartData = this.analyzeDataForChart(results);
        
        if (!chartData) {
            this.displayChartError('Bu sorgu verisi grafik oluşturmak için uygun değil');
            return;
        }

        this.displayChartError(''); // Başarılıysa hata mesajını temizle
        // Hide placeholder, show canvas
        const placeholder = this.elements.chartContainer.querySelector('.no-chart-placeholder');
        if (placeholder) placeholder.style.display = 'none';
        this.elements.dashboardChart.style.display = 'block';

        // Destroy existing chart
        if (this.state.currentChart) {
            this.state.currentChart.destroy();
        }

        // Create new chart
        const ctx = this.elements.dashboardChart.getContext('2d');
        // Fix hover-induced layout shifts by enforcing height and disabling aspect ratio
        this.elements.dashboardChart.height = 420;

        // Use user-selected type only
        const typeToUse = this.state.selectedChartType;
        const chartConfig = this.getChartConfig(chartData, typeToUse);
        
        this.state.currentChart = new Chart(ctx, chartConfig);

        // Show chart info
        this.displayChartInfo(query, chartData);
        this.highlightRecommendedChart(chartData.recommendedChart);
        
        this.showStatus('Grafik başarıyla oluşturuldu!', 'success');
    }

    highlightRecommendedChart(recommended) {
        if (!this.elements.chartTypeBtns) return;
        this.elements.chartTypeBtns.forEach(btn => {
            const type = btn.dataset.chartType;
            // Add star to the recommended button label and a badge
            if (recommended && type === recommended) {
                btn.classList.add('recommended');
                if (!btn.dataset.originalText) btn.dataset.originalText = btn.textContent;
                btn.textContent = `${btn.dataset.originalText} · Önerilen`;
                btn.style.backgroundColor = '#fde7c6'; // light orange background
                btn.style.borderColor = '#f59e0b';
            } else {
                btn.classList.remove('recommended');
                if (btn.dataset.originalText) btn.textContent = btn.dataset.originalText;
                btn.style.backgroundColor = '';
                btn.style.borderColor = '';
            }
        });
    }

    async showRecommendedChartForSelectedQuery() {
        try {
            const response = await this.apiCall(`/queries/${this.state.selectedQueryId}`, 'GET');
            if (!response.success || !response.data || !response.data.query_results) {
                this.highlightRecommendedChart(null);
                return;
            }
            const results = response.data.query_results;
            const chartData = this.analyzeDataForChart(results);
            if (!chartData) {
                this.highlightRecommendedChart(null);
                return;
            }
            // Mark recommended in UI but DO NOT set selected type
            this.highlightRecommendedChart(chartData.recommendedChart);
        } catch (e) {
            this.highlightRecommendedChart(null);
        }
    }

    /**
     * Analyze data to determine best chart representation
     */
    analyzeDataForChart(results) {
        if (results.length === 0) return null;

        const keys = Object.keys(results[0]);
        
        if (keys.length < 2) return null;

        // Try to find label and value columns
        let labelKey = null;
        let valueKey = null;

        // Look for numeric columns
        const numericKeys = keys.filter(key => {
            const value = results[0][key];
            return typeof value === 'number' || !isNaN(parseFloat(value));
        });

        // Look for string/text columns for labels
        const textKeys = keys.filter(key => !numericKeys.includes(key));

        if (numericKeys.length === 0) {
            // No numeric data, try to count occurrences
            labelKey = keys[0];
            const counts = {};
            results.forEach(row => {
                const label = String(row[labelKey]);
                counts[label] = (counts[label] || 0) + 1;
            });
            
            return {
                labels: Object.keys(counts),
                datasets: [{
                    label: 'Count',
                    data: Object.values(counts)
                }]
            };
        }

        // Use first text column as label, first numeric as value
        labelKey = textKeys.length > 0 ? textKeys[0] : keys[0];
        valueKey = numericKeys[0];

        const labels = results.map(row => String(row[labelKey]));
        const data = results.map(row => parseFloat(row[valueKey]) || 0);

        // Heuristic recommendation
        const distinctLabelCount = new Set(labels).size;
        let recommended = 'bar';
        if (distinctLabelCount <= 5 && distinctLabelCount > 0) {
            recommended = 'pie';
        } else if (distinctLabelCount > 15) {
            recommended = 'line';
        }

        return {
            labels: labels,
            datasets: [{
                label: valueKey,
                data: data
            }],
            originalKeys: { labelKey, valueKey },
            recommendedChart: recommended
        };
    }

    /**
     * Get chart configuration
     */
    getChartConfig(chartData, chartType) {
        const colors = [
            'rgba(37, 99, 235, 0.8)',
            'rgba(16, 185, 129, 0.8)',
            'rgba(239, 68, 68, 0.8)',
            'rgba(245, 158, 11, 0.8)',
            'rgba(139, 92, 246, 0.8)',
            'rgba(236, 72, 153, 0.8)',
            'rgba(14, 165, 233, 0.8)',
        ];

        const backgroundColors = chartType === 'pie' || chartType === 'doughnut' 
            ? colors 
            : colors[0];

        const config = {
            type: chartType === 'area' ? 'line' : chartType,
            data: {
                labels: chartData.labels,
                datasets: chartData.datasets.map((dataset, index) => ({
                    label: dataset.label,
                    data: dataset.data,
                    backgroundColor: Array.isArray(backgroundColors) ? backgroundColors : backgroundColors,
                    borderColor: Array.isArray(backgroundColors) ? backgroundColors.map(c => c.replace('0.8', '1')) : backgroundColors.replace('0.8', '1'),
                    borderWidth: 2,
                    fill: chartType === 'area'
                }))
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 400 },
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                    },
                    title: {
                        display: false
                    }
                },
                scales: chartType !== 'pie' && chartType !== 'doughnut' ? {
                    y: {
                        beginAtZero: true
                    }
                } : {}
            }
        };

        return config;
    }

    

    /**
     * Display chart information
     */
    displayChartInfo(query, chartData) {
        if (!this.elements.chartInfo || !this.elements.chartDetails) return;

        const totalDataPoints = chartData.labels.length;
        const dataset = chartData.datasets[0];
        const total = dataset.data.reduce((a, b) => a + b, 0);
        const average = (total / dataset.data.length).toFixed(2);
        const max = Math.max(...dataset.data);
        const min = Math.min(...dataset.data);

        this.elements.chartDetails.innerHTML = `
            <div class="chart-stat">
                <div class="chart-stat-label">Veri Sayısı</div>
                <div class="chart-stat-value">${totalDataPoints}</div>
            </div>
            <div class="chart-stat">
                <div class="chart-stat-label">Toplam</div>
                <div class="chart-stat-value">${total.toFixed(2)}</div>
            </div>
            <div class="chart-stat">
                <div class="chart-stat-label">Ortalama</div>
                <div class="chart-stat-value">${average}</div>
            </div>
            <div class="chart-stat">
                <div class="chart-stat-label">Maksimum</div>
                <div class="chart-stat-value">${max}</div>
            </div>
            <div class="chart-stat">
                <div class="chart-stat-label">Minimum</div>
                <div class="chart-stat-value">${min}</div>
            </div>
        `;

        this.elements.chartInfo.style.display = 'block';
    }

    displayChartError(message) {
        const el = this.elements.chartContainer.querySelector('#chart-error-message');
        if (el) {
            if (message) {
                el.textContent = message;
                el.style.display = 'block';
            } else {
                el.textContent = '';
                el.style.display = 'none';
            }
        }
    }
}

// Initialize application when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // DOM tamamen yüklendikten sonra uygulamayı başlat
    setTimeout(() => {
        window.app = new NIQApp();
    }, 100); // 100ms gecikme ile DOM elementlerinin hazır olduğundan emin ol
});

document.addEventListener('DOMContentLoaded', function () {
    var copyBtn = document.getElementById('copy-server-ip-btn');
    var ipCmd = document.getElementById('server-ip-cmd');
    if(copyBtn && ipCmd) {
        copyBtn.addEventListener('click', function () {
            navigator.clipboard.writeText(ipCmd.textContent.trim());
            copyBtn.classList.add('copied');
            copyBtn.textContent = 'Kopyalandı!';
            setTimeout(function () {
                copyBtn.textContent = 'Kopyala';
                copyBtn.classList.remove('copied');
            }, 1000);
        });
    }

    var ipHelpBtn = document.getElementById('ip-help-toggle');
    var hintBox = document.querySelector('.server-hint-box');
    if (ipHelpBtn && hintBox) {
        ipHelpBtn.addEventListener('click', function () {
            var willShow = hintBox.style.display === 'none' || hintBox.style.display === '';
            hintBox.style.display = willShow ? 'block' : 'none';
            ipHelpBtn.textContent = willShow ? 'Kapat' : 'IP öğren';
            if (willShow) {
                ipHelpBtn.classList.remove('pulse');
            } else {
                ipHelpBtn.classList.add('pulse');
            }
        });
    }
});
